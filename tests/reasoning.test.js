const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'hlwy-ai-checker.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const plain = value => JSON.parse(JSON.stringify(value));

function harness(respond) {
    const elements = new Map();
    const stored = new Map();
    const requests = [];
    function el(id) {
        if (!elements.has(id)) elements.set(id, {
            value: '', checked: false, style: {}, dataset: {}, children: [], innerHTML: '',
            classList: { add() {}, remove() {} },
            appendChild(child) { this.children.push(child); },
        });
        return elements.get(id);
    }
    const context = vm.createContext({
        console,
        localStorage: { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) },
        window: { addEventListener() {} },
        document: { getElementById: el, createElement: () => ({ style: {} }) },
        prompt: () => 'saved', confirm: () => true, alert: message => { throw new Error(message); },
        fetch: async (url, options) => {
            const body = options?.body ? JSON.parse(options.body) : undefined;
            requests.push({ url, body });
            const data = respond ? respond(url, body) : url === '/messages'
                ? { content: [{ type: 'thinking', thinking: '99' }, { type: 'text', text: '217' }], stop_reason: 'end_turn' }
                : url === '/responses'
                    ? { output: [{ type: 'reasoning' }, { type: 'message', content: [{ type: 'output_text', text: '217' }] }] }
                    : { choices: [{ message: { content: '217' }, finish_reason: 'stop' }] };
            return { ok: true, json: async () => data };
        },
    });
    const run = code => vm.runInContext(code, context);
    run(script);
    run('displayCalibrationChart = displayComparisonChart = displayQuickChart = displayCompareChart = () => {};');
    function configure(prefix, apiType = 'openai', mode = 'high') {
        const channelId = prefix.startsWith('ch-') ? prefix.slice(3) : null;
        el(channelId ? 'ch-api-type-' + channelId : prefix + '-api-type').value = apiType;
        el(prefix + '-reasoning-mode').value = mode;
        el(prefix + '-reasoning-effort').value = 'high';
        el(prefix + '-thinking-budget').value = '4096';
        el(prefix + '-max-output-tokens').value = '';
        el(channelId ? 'ch-base-url-' + channelId : prefix + '-base-url').value = 'https://example.invalid/v1';
        el(channelId ? 'ch-api-key-' + channelId : prefix + '-api-key').value = 'test-key';
        el(channelId ? 'ch-model-' + channelId : prefix + '-model-name').value = 'test-model';
        el(channelId ? 'ch-header-preset-' + channelId : prefix + '-header-preset').value = 'default';
        el(prefix + '-iterations').value = '50';
        el(prefix + '-concurrency').value = '3';
    }
    return { run, el, requests, configure, stored };
}

function baseline(reasoning) {
    const distribution = Array(355).fill(0);
    distribution[216] = 1;
    return {
        name: 'fixture', model: 'test-model', apiType: 'openai',
        timestamp: '2026-11-01T00:00:00Z', iterations: 50, distribution,
        stats: { mean: 217, median: 217, stdDev: 0, mode: 217, modeCount: 50, unique: 1 },
        ...(reasoning ? { reasoning } : {}),
    };
}

test('default bodies retain the original sampling parameters for all APIs', () => {
    const h = harness();
    for (const api of ['openai', 'openai-responses', 'anthropic']) {
        const body = plain(h.run('buildRequestBody(' + JSON.stringify(api) + ', "test-model")'));
        assert.equal(body.temperature, 1);
        assert.equal(body.max_tokens ?? body.max_output_tokens, 256);
        for (const key of ['reasoning_effort', 'reasoning', 'thinking', 'output_config', 'max_completion_tokens']) {
            assert.equal(body[key], undefined);
        }
    }
});

test('OpenAI efforts use the correct fields and preserve a custom token limit', async () => {
    for (const api of ['openai', 'openai-responses']) {
        for (const mode of ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']) {
            const h = harness();
            const config = JSON.stringify({ mode, maxTokens: 8192 });
            assert.equal(await h.run('getRandomNumber("' + api + '", "url", "key", "model", "default", ' + config + ')'), 217);
            const { url, body } = h.requests[0];
            assert.equal(url, api === 'openai' ? '/chat/completions' : '/responses');
            assert.equal(body.reasoning_effort ?? body.reasoning.effort, mode);
            assert.equal(body.max_completion_tokens ?? body.max_output_tokens, 8192);
            assert.equal(body.temperature, undefined);
            assert.equal(body.max_tokens, undefined);
        }
    }
});

test('Claude supports adaptive effort, disabled thinking, and manual budgets', async () => {
    for (const effort of ['default', 'low', 'medium', 'high', 'xhigh', 'max']) {
        const h = harness();
        const config = JSON.stringify({ mode: 'adaptive', effort });
        assert.equal(await h.run('getRandomNumber("anthropic", "url", "key", "model", "default", ' + config + ')'), 217);
        const body = h.requests[0].body;
        assert.deepEqual(body.thinking, { type: 'adaptive' });
        assert.equal(body.output_config?.effort, effort === 'default' ? undefined : effort);
        assert.equal(body.max_tokens, 16384);
        assert.equal(body.temperature, undefined);
    }
    const h = harness();
    const manual = plain(h.run("buildRequestBody('anthropic', 'model', {mode:'enabled', budgetTokens:4096})"));
    assert.deepEqual(manual.thinking, { type: 'enabled', budget_tokens: 4096 });
    assert.equal(manual.max_tokens, 4352);
    assert.deepEqual(plain(h.run("buildRequestBody('anthropic', 'model', {mode:'disabled'})")).thinking, { type: 'disabled' });
});

test('invalid budgets, modes, and token limits are rejected', () => {
    const h = harness();
    for (const raw of [
        { mode: 'enabled', budgetTokens: 1023 }, { mode: 'enabled', budgetTokens: 4096, maxTokens: 4096 },
        { mode: 'adaptive', effort: 'ultra' }, { mode: 'adaptive', maxTokens: 100.5 },
        { mode: 'adaptive', maxTokens: 131073 }, { mode: 'high' },
    ]) assert.equal(h.run('normalizeReasoningConfig(' + JSON.stringify(raw) + ', "anthropic")'), null);
    assert.equal(h.run("normalizeReasoningConfig({mode:'adaptive'}, 'openai')"), null);
});

test('truncated output is rejected even if it contains a parseable number', async () => {
    const payloads = [
        ['openai', { choices: [{ message: { content: '217' }, finish_reason: 'length' }] }],
        ['anthropic', { content: [{ type: 'text', text: '217' }], stop_reason: 'max_tokens' }],
        ['openai-responses', { status: 'incomplete', incomplete_details: { reason: 'max_output_tokens' } }],
    ];
    for (const [api, payload] of payloads) {
        const h = harness(() => payload);
        await assert.rejects(h.run('getRandomNumber("' + api + '", "url", "key", "model")'), /token 上限不足/);
    }
});

test('unsupported reasoning surfaces the API error without retrying at lower effort', async () => {
    const h = harness();
    h.run("fetch = async () => { globalThis.calls = (globalThis.calls || 0) + 1; return {ok:false, status:400, text:async () => 'reasoning_effort high is unsupported'}; };");
    await assert.rejects(h.run("callOpenAI('url', 'key', 'model', 'default', {mode:'high'})"), /400.*reasoning_effort/);
    assert.equal(h.run('calls'), 1);
    assert.equal(h.run("classifyError(new Error('API错误: 400 - model does not support reasoning_effort')).badge"), '思考参数');
});

test('baseline import retains reasoning metadata and keeps legacy settings unknown', () => {
    const h = harness();
    const config = { mode: 'high', effort: 'default', budgetTokens: 0, maxTokens: 16384 };
    const normalized = plain(h.run('normalizeDownloadedBaseline([' + JSON.stringify(baseline(config)) + '])'));
    assert.deepEqual(normalized.reasoning, config);
    assert.equal(h.run('normalizeBaseline(' + JSON.stringify(baseline()) + ').reasoning'), undefined);
    assert.equal(h.run('normalizeBaseline(' + JSON.stringify(baseline({ mode: 'invalid' })) + ')'), null);
    const saved = JSON.stringify(normalized);
    assert.equal(h.run('reasoningMatches(' + saved + ', "openai-responses", ' + JSON.stringify(config) + ')'), true);
    assert.equal(h.run('reasoningMatches(' + saved + ', "openai", ' + JSON.stringify({ ...config, maxTokens: 8192 }) + ')'), false);
    assert.match(h.run('configuredVerdict(1, ' + JSON.stringify(baseline()) + ', "openai", ' + JSON.stringify(config) + ').text'), /未知/);
    assert.match(h.run('configuredVerdict(1, ' + saved + ', "openai", ' + JSON.stringify({ ...config, mode: 'low' }) + ').text'), /配置不同/);
});

test('calibration snapshots all requests and saves settings for subsequent testing', async () => {
    const h = harness();
    h.configure('calib');
    h.el('calib-baseline-name').value = 'high baseline';
    h.run("const originalFetch = fetch; fetch = async (...args) => { document.getElementById('calib-reasoning-mode').value = 'low'; return originalFetch(...args); };");
    await h.run('startCalibration()');
    assert.equal(h.requests.length, 50);
    assert.ok(h.requests.every(r => r.body.reasoning_effort === 'high' && r.body.max_completion_tokens === 16384));
    const saved = JSON.parse(h.stored.get('modelBaselines'));
    assert.equal(saved[0].reasoning.mode, 'high');
    assert.equal(saved[0].iterations, 50);
    h.configure('test', 'openai-responses');
    await h.run('startTest()');
    assert.ok(h.requests.slice(50).every(r => r.body.reasoning.effort === 'high'));
    assert.match(h.el('test-matches').children[0].innerHTML, /模型一致/);
});

test('one-click testing sends reasoning and marks legacy baselines as reference only', async () => {
    const fixture = baseline();
    const h = harness(url => url.startsWith('/api/baseline')
        ? { data: [fixture], source: 'fixture' }
        : { choices: [{ message: { content: '217' }, finish_reason: 'stop' }] });
    h.configure('quick');
    h.el('quick-model-select').value = 'claimed';
    h.run("quickModels = [{id:'claimed'}, {id:'other'}];");
    await h.run('startQuickCheck()');
    const calls = h.requests.filter(r => r.url === '/chat/completions');
    assert.equal(calls.length, 50);
    assert.ok(calls.every(r => r.body.reasoning_effort === 'high'));
    const report = h.el('quick-report').innerHTML;
    assert.match(report, /基准思考配置未知，仅供参考/);
    assert.match(report, /本次思考配置/);
    assert.doesNotMatch(report, /qv-text">模型一致|qv-text">模型可能被替换/);
});

test('channel comparison forwards each channel thinking configuration', async () => {
    const h = harness();
    h.configure('ch-1', 'anthropic', 'adaptive');
    h.el('ch-name-1').value = 'channel';
    h.el('compare-baseline-select').value = '0';
    h.el('compare-iterations').value = '50';
    h.el('compare-concurrency').value = '3';
    h.run('baselines = [' + JSON.stringify(baseline()) + ']; compareChannels = [{id:1}];');
    await h.run('startCompare()');
    assert.equal(h.requests.length, 50);
    assert.ok(h.requests.every(r => r.body.thinking.type === 'adaptive' && r.body.output_config.effort === 'high'));
    assert.match(h.el('compare-ranking').innerHTML, /基准思考配置未知，仅供参考/);
});

test('API profiles restore reasoning and old profiles reset it to default', async () => {
    const h = harness();
    h.configure('calib', 'anthropic', 'adaptive');
    await h.run("saveApiProfile('calib')");
    const profiles = JSON.parse(h.stored.get('hlwy_api_profiles'));
    assert.equal(profiles[0].reasoning.effort, 'high');
    h.configure('test');
    h.el('test-profile-select').value = 'saved';
    await h.run("loadApiProfile('test')");
    assert.equal(h.el('test-api-type').value, 'anthropic');
    assert.equal(h.el('test-reasoning-mode').value, 'adaptive');
    assert.equal(h.el('test-reasoning-effort').value, 'high');
    assert.equal(h.el('test-max-output-tokens').value, '');
    assert.equal(h.run("readReasoningConfig('test').maxTokens"), 16384);
    profiles[0].reasoning.maxTokens = 8192;
    profiles[0].reasoningMaxTokensAuto = false;
    h.stored.set('hlwy_api_profiles', JSON.stringify(profiles));
    await h.run("loadApiProfile('test')");
    assert.equal(h.el('test-max-output-tokens').value, 8192);
    delete profiles[0].reasoning;
    h.stored.set('hlwy_api_profiles', JSON.stringify(profiles));
    await h.run("loadApiProfile('test')");
    assert.equal(h.el('test-reasoning-mode').value, 'default');
    assert.equal(h.el('test-max-output-tokens').value, '');
    assert.equal(h.run("readReasoningConfig('test').maxTokens"), 256);
});

test('replacement verdicts exclude stronger matches with different or unknown reasoning', () => {
    const config = { mode: 'high', effort: 'default', budgetTokens: 0, maxTokens: 16384 };
    for (const otherConfig of [undefined, { ...config, mode: 'low' }, config]) {
        const h = harness();
        const primary = baseline(config);
        const similarity = { overallScore: 0.95, cosineSimilarity: 0.9, jsDivergence: 0.1 };
        const report = {
            modelId: 'claimed', model: 'test-model', apiType: 'openai', reasoning: config,
            baseline: primary, baselineSource: 'fixture', similarity, stats: primary.stats,
            distribution: primary.distribution, iterations: 50, successCount: 50, errorCount: 0,
            crossMatches: [
                { id: 'other', baseline: baseline(otherConfig), similarity: { ...similarity, overallScore: 1 }, isClaimed: false },
                { id: 'claimed', baseline: primary, similarity, isClaimed: true },
            ], crossFailed: [],
        };
        h.run('displayQuickResult(' + JSON.stringify(report) + ')');
        assert.equal(h.el('quick-report').innerHTML.includes('qv-text">模型可能被替换'), otherConfig === config);
    }
});
