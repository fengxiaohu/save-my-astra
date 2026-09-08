# Phase 3A — Local Coding Delegation A/B 实验计划

版本：v1.1，2026-09-08。两臂 parent effort 统一为 medium。八项实验前置能力已进行代码实现和离线验证；尚未执行真实 Gate 0 模型探测或正式实验。操作入口与剩余运行时验证见 [docs/phase3a.md](docs/phase3a.md)。

## 1. 目标和结论边界

比较同一任务上的 Astra Solo 与 Astra + Luna standing policy，回答：

1. Astra 是否自主创建 child，并委派实际工作？
2. 所有实际 child 是否确认为 Luna，是否出现 Astra clone？
3. 本次配对是否观察到质量回归；Astra parent 的绝对 token 消耗是否下降？

这是预先选定四题、每臂每题一次的 feasibility pilot。可以报告逐题结果、描述性比例和中位数；不能证明稳定成功率、统计非劣性、完整 TB4 水平、美元成本或 Plus 额度节省。Token 是工作量代理指标，不等价于算力、思考深度或 Plus 计费量。

比较对象是两套完整策略（solo 与配置化 delegation），不能独立识别 worker 模型、并行或 standing instruction 各自的贡献。Baseline 禁用 agents，因此“零 Astra child”仅表示 treatment 中未观察到 clone，不证明相较默认 multi-agent 行为减少了多少 clone。

## 2. 对两版方案的取舍

| 内容 | 最终决定 |
| --- | --- |
| 两臂 parent 都用 Astra / medium | 按最新要求统一调整，避免两臂使用不同 parent effort |
| Treatment 用 Luna / max | 采纳，四题一致，模型和 effort 都固定 |
| Terra fallback | 主实验禁用；不可用就记录 model_unavailable |
| 原始任务指令、自主拆分 | 采纳；不指定 spawn 数、不提供逐题拆分图 |
| 四题 × 两臂 × 一次 | 保留，最多八次 primary runs |
| 分两轮，先检查 routing | 保留；统一 Gate 1，不再使用“T1 不 spawn 就停”的另一条规则 |
| Parent Token Share | 降为辅助指标；核心改成同题 parent 绝对 token 配对 |
| Routed resolve ≥ Solo − 1 | 删除；逐题报告回归，不能称作质量保持 |
| 所有可验证 child 为 Luna 即通过 | 收紧；任何 unknown 都不能证明零 clone |
| 全部环境压力记 environment_failure | 修订；并发诱发的资源失败保留为配置结果 |
| 安装日常 profile 后跑 A/B | 不采用；每个 run 使用独立实验配置，不切换全局日常配置 |

## 3. 模型、策略和运行时冻结

| 项目 | A：astra-solo | B：astra-luna |
| --- | --- | --- |
| Parent | gpt-6-astra | gpt-6-astra |
| Parent effort | medium | medium |
| Agents | disabled | enabled |
| Worker | 无 | luna_max_worker |
| Worker model / effort | 无 | gpt-5.6-luna / max |
| Fallback | 无 | 禁用 |
| Child context | 无 | fork_turns: none，完整独立 brief |
| Child 层级 | 无 | 仅 parent 创建 worker；worker 不再创建子代理 |
| 并发政策 | 一个 parent | 最多一个 parent + 三个同时存活的 worker |

并发上限是容量限制，不是要求创建三名 worker。Astra 自行决定是否委派、数量和职责。顺序创建的 child 总数不预先限定，全部记录。必须确认所固定 CLI 版本中配置字段的实际计数语义，不能仅凭字段名假设“4”包含 parent。

两臂保留相同的任务完成、工具使用和验收要求。实验专用 policy 仅在 delegation 能力及相关策略上不同；baseline 不保留要求其委派的矛盾指令。worker 可执行有明确边界的调查、修改和验证，Astra 负责分工、整合与最终验收。

固定 ChatGPT Plus 认证和 Codex CLI；不使用 API key 认证，不引用 API 价格作为本阶段成本。主执行链路为 Harbor → 固定版本 Codex agent/CLI → 任务环境 → 原始 verifier。只向 codex exec 提供任务名字或摘要不算完成 TB4 实验。

固定宿主机、OS、CPU 架构、Docker/Harbor/CLI 版本、agent adapter 版本、任务快照、依赖及镜像 digest、配置和 policy 哈希。模型标识固定不保证服务端快照永久固定；记录时间和可见模型版本，遇到已知服务变更停止混合运行。

## 4. 当前仓库与计划之间的缺口

以下列出原审阅缺口及本轮实现状态；静态实现不代替真实 Gate 0：

| 位置 | 观察 | 正式运行前的要求 |
| --- | --- | --- |
| profiles 与 policy | 日常 low 保留；phase3_config 派生两臂 medium，使用独立实验 policy | Gate 0 验证实际加载与子代理能力 |
| fallback 与 baseline | 实验禁用 fallback，baseline 不生成 worker 文件或委派指令 | Gate 0 验证运行时不会继承日常策略 |
| spawn / usage | 已改为结构化创建证据、线程归因和完整性标记 | 用真实 CLI 日志验证当前版本语义 |
| Harbor 双臂执行 | 新增冻结的八步 runner；旧入口禁止静默单臂执行 | 实际 Harbor 兼容性仍待 Gate 0 |
| 四题清单 | 新增 phase3a 显式清单，禁止截断；本地任务树哈希冻结 | 尚需真实四题快照和环境预检 |
| 认证 / 超时 / 清理 | 临时认证上传、独立配置、agent/verifier 上限、quota 监督、专属 Docker 清理已接入 | 不把离线模拟当成认证或容器已验证 |
| 配对报告 | 逐题终止状态、原始 reward、parent reduction、身份及 incomplete 已实现，美元为 null | 正式结果目前为空，未执行实验 |

使用独立的 `phase3a prepare / inspect / execute` 入口。prepare 只冻结已有本地输入；execute 必须具备与冻结绑定的 Gate 0 证据、镜像 digest 和有效 telemetry。不要使用默认 smoke 或日常安装命令代替这一流程。

## 5. Gate 0：正式实验的前置验收

### 5.1 四题环境预检

固定清单，不根据模型答题表现换题：

1. T1：session-window-debug
2. T2：payments-pipeline-fix
3. T3：bun-sourcemap-leak
4. T4：nextjs-performance

先确认四个 ID 存在于选定的数据集快照，取得完整任务、环境定义和原始 verifier。四题描述及 M1 兼容性不能仅凭本计划假定成立。

在不调用模型的情况下完成容器启动、依赖准备、所需服务和 verifier 可执行性检查。原始有缺陷代码未通过 verifier 可以是预期现象；关注 verifier 能否正常执行，不能为“修好预检”修改任务或隐藏验收要求。

默认资源预算建议：Docker VM 6 vCPU / 12 GiB，总是串行执行 runs；预检若证实不足，可在首个 primary run 前调整并冻结。记录所有 task 容器的限制、架构和模拟执行情况。不同任务可有不同固定需求，同题两臂必须一致。

若某题无法运行，记录 preflight_blocked；不静默换题。先解决兼容性，或另立清楚标记的缩小版协议，不能对未完成四题宣称达到四题标准。

### 5.2 Harness 验收

通过独立于正式四题的最小 smoke，确认：

- Plus 认证可到达实际 agent 进程；不输出 credential、不进入报告或 artifact，临时凭证使用受限权限，成功和异常结束均清理。
- CLI 正确加载 parent/effort、agents 开关和 worker 配置；baseline 的 child 能力实际关闭。
- Treatment 能创建并识别 Luna child；记录 parent-child 关联、实际模型证据及成功/失败事件。此 smoke 可明确要求 spawn，但其结果不计入自主 delegation 指标。
- 两臂产物、退出状态和 verifier 结果均能关联唯一 run ID；primary 失败也能保存证据。
- usage 来源和统计范围已说明：每 turn 增量还是累计、parent 是否包含 child、缓存和 reasoning 字段是否为子集，如何去重。

若无法观察真实 child 创建和模型身份，Gate 0 不通过。若 routing 可核验，但 token 收集或归因仍不完整，可将协议显式标记为 routing-only 后运行；Q3 token 结论必须为 inconclusive，不能事后补造数字。

Smoke 与预检的时间、用量单列，不计入八次 primary，但计入账户止损。Gate 0 完成后写入全部实际版本、数值与哈希，生成 frozen manifest；未填写关键字段不启动正式实验。

## 6. 隔离、预算和执行顺序

每个 task × arm 使用独立初始工作区、容器/服务数据、CODEX_HOME 和新会话。保持同题原始输入完全一致，禁止跨臂读取改动、日志、报告、对话或 child findings。实验报告目录不暴露给答题 agent。

依赖和基础镜像提前准备，两臂使用相同预热策略。应用 build、业务数据、测试缓存等可影响结果的状态，在每次运行前恢复为相同初始状态。共享宿主机缓存难以彻底消除的影响如实记录。

Agent 阶段的固定上限为每 run 60 分钟；若任务自身规定更短上限则取更短者。包括 parent 等待 child 的时间，子代理共享同一 run 截止时间。超时停止所有 child，并按协议运行原始 verifier；记录 partial artifact 的验收结果。环境准备与 verifier 使用冻结的独立上限，不能无界等待。

这是本地预算受限结果；若比官方默认预算短，不声称是官方标准配置成绩。主实验不另设一个两臂不对称的 token 上限。

按下表预先固定顺序，同一时刻只跑一个 run：

| 序号 | 任务 | Arm |
| --- | --- | --- |
| 1 | T1 session-window-debug | A：Solo |
| 2 | T1 session-window-debug | B：Luna |
| 3 | T2 payments-pipeline-fix | B：Luna |
| 4 | T2 payments-pipeline-fix | A：Solo |
| — | Gate 1 | 检查前四次 |
| 5 | T3 bun-sourcemap-leak | A：Solo |
| 6 | T3 bun-sourcemap-leak | B：Luna |
| 7 | T4 nextjs-performance | B：Luna |
| 8 | T4 nextjs-performance | A：Solo |

此顺序平衡先后效应，不是随机样本。暂停后恢复下一个计划 run，不因结果改变顺序、effort、任务提示或拆分策略。

## 7. Gate 1、异常与止损

完成前四次有效运行后：若 T1/T2 的 treatment 至少一个成功创建经确认的 Luna child，且没有配置违例，则继续。两个都未创建 child，则结束本版 pilot，记录 routing_gate_failed；T3/T4 标 not_run。

未创建 child 不等于一定是配置故障，也可能是本次自主决策。先依据事件区分未尝试、尝试被拒绝、创建失败、观察缺失，再调查原因。

任何时间出现 baseline 创建 child、Astra/Terra child、配置漂移：停止后续运行，保留该异常作为策略/协议失败证据。不得只是剔除异常 run 后宣称“零 clone”。修改策略后新建 protocol version，不能拼接修改前后的八次成绩。

Child 身份 unknown 或事件链缺失：标 observation_incomplete，暂停以检查观测链，不能当作未 spawn 或零 clone。Luna 不可用：标 model_unavailable，不 fallback，保留原始尝试。

| 情况 | 处理 |
| --- | --- |
| 正常执行但 verifier 不通过 | quality FAIL，不重试择优 |
| 模型修改导致构建失败、agent 自身错误、并行诱发 OOM/超时 | 保留为配置在本机的结果；记录具体终止原因 |
| 已证实无关的断网、宿主机故障、基础设施中断 | infrastructure_error，不能凭“内存高”推定 |
| 止损中断了尚未完成的 run | budget_aborted，验收可另记，不能视为完整配对 |
| 尚未执行的任务 | not_run，不算作答题失败 |

独立基础设施故障允许每个 task × arm 最多一次 replacement，旧记录不删除，明确 replacement_of。所有消耗计入止损；正式计划八次 primary 与额外尝试分开统计。不得因为不 spawn、token 高或质量失败而重跑。再次基础设施故障则停止该配对，结论 incomplete。

Plus 只用于观察和止损：同一 5h 窗口相对开始读数增加 ≥20 个百分点，不启动下一次；达到 ≥30 个百分点，停止当前运行并停止当天实验。读数使用 used percent，记录 limit ID、采样时间和 resetsAt，不能跨 reset 相减。窗口内其他账户活动、延迟或缺失使其无法解释时，不能当成 benchmark 归因消耗；读数不可用则暂停新 run，先恢复观察。

持续红色 memory pressure、严重 swap 或热降频时停止启动新 run；若中断当前 run，记录实际状态和原因。是否构成策略资源失败按证据判断，不事后删除 treatment 的不利表现。

## 8. 每次运行的操作清单

1. 确认 quota、资源和顺序允许启动，准备独立初始环境。
2. 保存 run manifest：protocol/run/task/arm/attempt ID，任务、verifier、配置和 policy 哈希，软件版本，实际限制，开始时间与 quota 观测。
3. 核验最终加载的 parent/effort、worker/effort、agents 开关、无 fallback。使用原始任务指令启动，不追加手工分工。
4. 采集结构化生命周期事件和各线程 usage；保留运行时模型证据，不用 worker 名字推断模型。
5. Agent 完成或预算终止后停止仍在运行的 child，保存最终 diff/artifact，以原始 verifier 评分。Agent 自述“通过”不算。
6. 保存结束状态、verifier 日志、所有子项分数/约束、时间分段、用量覆盖和资源记录；清理临时认证及运行环境。
7. 更新逐题结果表，检查 Gate/止损；不基于结果修改下一次配置。

## 9. 指标口径

### Routing 和模型身份

- spawn_attempts：真实工具调用尝试数。
- spawn_count：成功创建的唯一 child 数，按 ID 去重；begin/end 事件、重发和文本提及不能重复计数。
- delegated task：spawn_count > 0。另记 verified_luna_delegated：成功创建的 child 身份确认为 Luna。
- completed_child_count / useful_child_count：完成并返回结果的数量，以及人工按证据判断对任务有贡献的数量；空 spawn 不能被描述成有效工作转移。
- 完整四题的 Delegation Rate：发生 delegation 的 treatment tasks / 4；路由门槛另要求至少三题 verified_luna_delegated。
- 提前结束时同时报告 planned=4、attempted、completed、delegated、not_run；可列 delegated/attempted，但不得冒充完整四题比例或通过 75% 门槛。
- Child 身份表：Luna / Astra / Terra / other / unknown，覆盖所有实际 descendants。模型证据区分 requested/configured 与 runtime-observed；未知保持未知。
- Clone Rate：身份全部确认且 child 总数 > 0 时，Astra child 数 / 全部 child 数。无 child 为 N/A；有 unknown 则整体是否零 clone 未证实。

### Quality

使用固定原始 verifier 的通过规则，并保留各约束或子项结果。报告两臂 PASS/FAIL 配对：双方 PASS、Solo PASS/Routed FAIL、Solo FAIL/Routed PASS、双方 FAIL。

Resolve count 可以描述为本次 selected-subset 的 x/4；相同计数不等于相同质量。任何 Solo PASS/Routed FAIL 都是本次观察到的回归，不能被另一题的改善抵销后称作“无回归”。

运行有效性、终止原因和 verifier 结果是不同字段：例如超时后 verifier 通过，也必须保留 timeout，不能将其伪装成正常完成。

### Token 和时间

以不重叠的 root/child 线程 usage 汇总，记录 input、cached input、output、可见 reasoning 和来源；仅在字段语义已确认时相加。Cached input 或 reasoning 若已包含在其他字段中，不再次相加。累计事件取差分或最终值，不能每个事件都累计一次总量。

Parent tokens 只含 root 线程，包括其消费 child 结果的上下文；child tokens 为全部 child 线程用量。单独报告 unknown/unobserved，缺失不填零。`spawn_count=0` 只有在确认事件完整、没有 child 且 runtime parent 已确认时才允许推断归因，并标注 inferred。

每题主要比较：

`parent_reduction_i = 1 - parent_tokens_routed_i / parent_tokens_solo_i`

正数为下降，零为不变，负数为增加；solo 为零或缺失则 N/A。先展示逐题绝对值和比例，再报告配对比例中位数。

完整运行全部展示，包括失败结果；另对双方正常完成且 verifier PASS 的配对做工作量补充分析。不得仅挑成功或发生 delegation 的 runs 作为主结果；后者至多为注明选择条件的诊断表。

Parent Token Share = parent / (parent + all children)，仅在用量收集与归因完整时计算，作为辅助描述，不作为工作量下降证据。若分母收集范围未知，则 total_tokens 也记 unavailable，只保留 observed usage。

Wall time 分别记录 setup、agent（包含等待 worker）、verifier 和 end-to-end；比较同题 agent 时间及 end-to-end，不能混用不同口径。允许 total tokens 上升，不把它单独作为失败条件。

USD estimate 在 Phase 3A 一律 null / not_evaluated；quota delta 不参与任何效果通过标准。

### 重复调查

人工记录 child 的 brief、证据、结果是否被 parent 使用，以及 parent 重读的目的。区分必要复核、整合读取和重复调查。未定义可靠的工作单位与分母前不输出 Duplicate Work Rate 百分比，不把同文件重读自动判成浪费。

## 10. 结果判定与 Phase 3B

先按维度给结论，不用单一 PASS 掩盖缺失证据：

| 维度 | Pilot 的操作性门槛 |
| --- | --- |
| 完整性 | 四题双臂均有有效运行和原始 verifier 结果；无混合协议 |
| Routing | 四个 treatment 中至少三个 verified_luna_delegated |
| 身份 | 所有 child 均确认为 Luna，无 fallback、Astra clone、递归 child 或 baseline child |
| Quality | 本次不存在 Solo PASS/Routed FAIL；保留全部配对，不宣称统计非劣 |
| Parent 信号 | 至少三个双方正常完成且 PASS 的配对具有完整 parent 用量；这些配对的 parent_reduction 中位数 > 0 |

“至少三个共同成功配对”是本 pilot 的证据量门槛，不是统计显著性或保证。没有预设最小节省幅度，因此即便刚刚大于零也只能称初步信号，报告实际幅度，不能称有实用价值。

- 全部达到：feasibility_pass，有理由进入 Phase 3B 隔离 Plus 窗口，仍不声称 savings 已被证明。
- Routing/身份达到，但 token 不完整或共同成功配对不足：partial / inconclusive；先补观测或在后续独立协议增加证据，不自动进入 quota 实验。
- 观察到质量回归、无 parent 下降信号或 routing 门槛未达：报告具体维度，先调查；不统一归因于 Luna 能力。
- 出现 clone/fallback/协议混合、关键观测缺失或环境无法执行：分别记策略失败、协议违例或 incomplete，不能列为整体通过。

Phase 3B 的 go 决策同时要求没有未解决的质量/资源问题，且下降幅度值得额外额度预算。Phase 3A 只提出 go/no-go 建议；不会自动启动 Phase 3B。

## 11. 交付目录与报告

建议每个 protocol version 独立目录：

```text
reports/phase3a/<protocol-id>/
  PLAN.md
  manifest.json
  preflight/
  runs/<task>/<arm>/<attempt-id>/
    manifest.json
    events.jsonl
    usage.json
    verifier-result.json
    timing-resources.json
    changes.diff
    raw/
  summary.json
  REPORT.md
```

认证文件永不进入上述目录。原始日志保留可审计证据，但存档前检查凭证泄露；答题 agent 不可见报告与 verifier 隐藏实现。

REPORT.md 先放四题配对表：task、两臂终止状态、两臂 verifier 结果、parent tokens、parent reduction、child 身份、spawn_count、两臂 agent time。随后给出 planned/attempted/completed、额外尝试、覆盖率和各维度结论。

最终只回答：是否自主委派、child 是否全部 Luna、是否观察到 clone、是否观察到质量回归、parent 工作量是否有下降信号、是否值得进入 Phase 3B。明确列出未运行项、缺失观测、资源失败和结论限制。
