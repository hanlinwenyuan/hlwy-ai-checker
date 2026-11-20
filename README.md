# hlwy-ai-checker
**检查第三方 AI API 是否掺假以及渠道一致性**

**可完全本地部署，保护 API Key 与测试数据**

# 使用指南

从 [Releases](https://github.com/hanlinwenyuan/hlwy-ai-checker/releases) 下载最新版本的 ZIP 文件并完整解压，然后运行：

```bash
python start.py
```
## 自动模式
### 直接使用一键测试功能，可以按照需求打开反针对模式

<img width="626" height="616" alt="image" src="https://github.com/user-attachments/assets/315279db-1c77-4b5e-aa79-42d5c6379df0" />


## 手动模式
### 1. 输入官方 API key 进行模型标定（Base URL 需包含 /v1）

<img width="1354" height="852" alt="图片" src="https://github.com/user-attachments/assets/67ff8592-dcf3-407c-9e12-57991447d016" />

### 2. 进行第三方渠道验证
填写第三方渠道的 API Key 和 Base URL，并选择与标定阶段相同的模型，然后开始测试。

### 3. 查看测试结果
测试完成后，比较官方渠道与第三方渠道的指纹相似度及相关统计结果。

## 思考强度设置

一键鉴别、标定基准、测试识别和大横评的每个渠道均可设置「思考强度 / 模式」以及「总输出 token 上限（含思考）」。默认选择「不指定」，沿用服务端默认行为；不指定不代表关闭思考。

- OpenAI 兼容格式发送 `reasoning_effort`，Responses API 发送 `reasoning.effort`。可选 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`，实际支持范围取决于模型和渠道。指定强度时省略 `temperature`，Chat Completions 使用 `max_completion_tokens`。
- Claude 新模型选择「自适应思考」，通过 `thinking.type: adaptive` 和 `output_config.effort` 设置强度；支持旧版扩展思考的模型可选择「手动思考预算」，预算至少为 1024 tokens，且必须小于总输出上限。是否支持关闭思考也取决于模型。
- 输出上限留空时：默认或关闭思考为 256 tokens，指定思考强度或自适应思考为 16384 tokens，手动思考为预算加 256 tokens。若收到输出截断错误，请提高上限。较高强度会增加耗时和 token 用量，思考请求的代理读取超时为 180 秒。
- API 不支持所选参数时会显示错误，程序不会自动删除思考参数或降低强度。服务端接受参数也不能证明渠道确实按该强度执行。

新标定的基准与保存的 API 配置会记录思考参数，基准导入、导出后也会保留。请用相同的 API 协议类型、思考配置和 token 上限进行标定与测试。旧基准没有记录这些参数，会显示「未记录（旧版基准）」；配置不同或未知时，相似度仅供参考，不给出模型被替换或降智的结论。可以重新标定后，在「测试识别」或「大横评」中比较。

参数说明见 [OpenAI 推理模型文档](https://developers.openai.com/api/docs/guides/reasoning)、[Claude effort 文档](https://platform.claude.com/docs/en/build-with-claude/effort)及[旧版扩展思考文档](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)。

开发验证：`node --test tests/reasoning.test.js` 和 `python3 -m unittest discover -s tests -p 'test_*.py'`，测试使用模拟 API，不消耗模型额度。

# 特色&优点

## 识别精确，区分度高
<img width="1463" height="599" alt="069a674bef0a8c3c0e1620c2573fc23d" src="https://github.com/user-attachments/assets/2081fd7c-040d-4512-aff3-755926d893e8" />

<img width="1447" height="607" alt="图片" src="https://github.com/user-attachments/assets/0141405c-7d23-4cf0-bbe6-3e3b8a3e9fce" />

## 一致性好，较少随机因素影响

<img width="1448" height="600" alt="0f64237c164f492dd0d677a97ba981f5" src="https://github.com/user-attachments/assets/07b00a61-ee17-4d39-bb32-8e367d0d03cd" />

## Token 消耗少

<img width="1663" height="290" alt="图片" src="https://github.com/user-attachments/assets/64e1f1a3-0796-4477-a1c0-1f3b004fdf4d" />

## 标定后再测试，自适应性强

<img width="1513" height="713" alt="图片" src="https://github.com/user-attachments/assets/2d2670b1-72ba-4cd1-9b5e-e3bf6a0d13b7" />



# 原理

大语言模型并不是真正的随机数生成器。当模型被要求“随机选择数字”时，其输出可能受到训练数据、模型架构、RLHF 对齐方式、分词策略及采样参数等因素影响，从而呈现不同的统计偏差。

通过重复采样并分析输出分布，可以形成具有一定区分度的统计指纹。将第三方渠道的测试结果与官方渠道的标定结果进行比较，可以辅助判断两者的行为特征是否一致。

该方法属于统计检测，测试结果会受到模型版本、采样参数、服务端配置、样本数量及请求成功率等因素影响，不能单独证明第三方渠道实际使用或未使用某一模型。

# 免责声明

测试结果仅供参考。

由于大模型本身存在随机性，且网络波动有影响，本工具的测试结果不能作为任何商业纠纷、退款索赔的绝对法律/事实依据。

本人仅作为开源代码维护者，不参与、不介入任何用户与 API 提供商之间的商业纠纷。

本项目由 [hanlinwenyuan](https://github.com/hanlinwenyuan) 开发，在 [Linux Do](https://linux.do/) 上发布。

# 友链

[LINUX DO - 新的理想型社区](https://linux.do/)
