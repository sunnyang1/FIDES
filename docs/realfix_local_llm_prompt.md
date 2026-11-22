# 本地大模型接续提示词：FIDES RealFix-30 实跑

将以下内容完整提供给本地大模型。它假设仓库已切换到
`feat/fides-eval-realfix` 分支；如果该分支已经合并，则在包含这些改动的
最新分支上工作。

```text
你是 FIDES 项目的高级研究工程师。请在本地仓库中继续完成 RealFix-30 的真实
模型执行与官方 SWE-bench Verified 评测，不要重写已经完成的评估基础设施。

一、项目目标

FIDES 是受治理的递归自我改进系统。当前需要验证的不是代理指标，而是：
在相同模型、scaffold、任务、随机种子和计算预算下，治理式完整归档搜索是否
能在真实 GitHub issue 修复中优于 base、普通迭代、贪心搜索和单线 FIDES。

二、必须先读取

1. AGENTS.md
2. docs/fides_eval_v0_1.md
3. experiments/realfix.py
4. experiments/realfix_experiment.py
5. experiments/exp_fides_realfix.py
6. experiments/exp_fides_realfix_compare.py
7. tests/test_realfix.py
8. tests/test_realfix_experiment.py
9. adl_lite/evolution/code_engine.py

先总结现有接口和未实现部分，再给出最小实施计划。不要重做 ProxyGauntlet、
ValleyPath、任务选择器、预测校验器或官方报告归一化器，除非复现出具体缺陷。

三、当前已完成并验证的事实

1. ProxyGauntlet：8/8 治理用例通过。
2. ValleyPath：证明受治理归档能保留中性桥接候选，同时阻止不安全晋升。
3. RealFix-30 选择器：从本地官方 JSONL 确定性选择 30 题，6 dev、24 held_out，
   按仓库轮询，manifest 不包含 gold patch 或 test patch。
4. 本地真实 Git smoke：9 个控制、11 项检查通过；覆盖合法修复、错误修复、
   空 patch、非法 patch、测试篡改、超时和运行器错误。
5. 五臂配置、预测覆盖校验、预测哈希绑定 run_id、官方 schema-v2 报告归一化
   和五臂比较已实现。
6. 当前治理与 RealFix 回归为 127 passed。
7. 上述合成 fixture 和本地 smoke 都不是官方 SWE-bench 成绩。
8. 当前没有真实模型预测、官方 Docker 运行、resolved 数量或 SWE-bench 分数。
9. 五臂的自主模型执行器尚未实现，这是你的主要工作。

四、环境与仓库

仓库：https://github.com/sunnyang1/FIDES
工作分支：feat/fides-eval-realfix

先检查工作区，不覆盖用户已有改动：

  git status
  git branch --show-current
  python3 -m pytest -q \
    tests/test_maat_governance.py \
    tests/test_governed_evolution.py \
    tests/test_phase2_artifacts.py \
    tests/test_phase3_code_gates.py \
    tests/test_phase3_code_engine.py \
    tests/test_fides_eval.py \
    tests/test_realfix.py \
    tests/test_realfix_experiment.py

如缺依赖，按项目 pyproject.toml 的 dev 依赖安装到本地虚拟环境，不修改系统
Python。确认 Docker 可用、磁盘空间足够，并安装当前官方 SWE-bench harness。
官方命令与字段以你实际安装版本的官方源码和文档为准，不凭记忆猜测。

五、执行步骤

阶段 A：获得真实任务清单

1. 从官方 SWE-bench/SWE-bench_Verified 数据集取得 test split，并导出 JSONL。
   每条至少包含 instance_id、repo、problem_statement、base_commit。
2. 记录数据集来源、版本或 commit、下载日期和文件 SHA-256。
3. 运行：

   python3 experiments/exp_fides_realfix.py select \
     --input /path/to/SWE-bench_Verified.jsonl \
     --output experiments/realfix_30_manifest.json

4. 验证恰好 30 个唯一 ID、6 dev、24 held_out，manifest 不含 gold/test patch。
5. 不手工替换选择结果。若选择器报错，保留错误并修复根因。

阶段 B：实现最小模型执行器

1. 使用同一个本地模型端点、同一 scaffold、同一 decoding 配置和相同预算。
2. 为每个任务生成官方预测字段：instance_id、model_name_or_path、model_patch。
3. 五臂必须真实执行不同搜索语义：
   - base：不进化的一次直接修复基线；
   - iterative：普通顺序迭代修复；
   - greedy：只保留当前最优候选；
   - fides_single_line：FIDES 治理与严格支配，但无探索归档；
   - fides_full_archive：FIDES 治理、严格支配晋升和探索归档。
4. base 若只调用一次模型，必须明确报告实际消耗低于其他臂；不能宣称实际
   compute 相等。预算上限可以相同，但实际 token、调用数、wall time、成本必须
   分别记录。必要时增加等消耗基线，不得隐藏预算差异。
5. dev 可用于调试验证；held_out 的 problem statement、test patch、gold patch、
   测试反馈不得进入调参或候选搜索过程。若设计上允许模型查看 issue 描述，
   只能使用官方任务定义允许提供给模型的字段，不得泄漏解答和隐藏测试。
6. 模型生成代码一律在官方容器或等价安全沙箱执行。临时 Git clone 只隔离
   文件状态，不是不可信代码的安全边界。
7. 保护评测测试和治理配置，不允许通过修改测试、评测器或超时配置制造通过率。
8. 先选 1 个 dev 任务做端到端 pilot，确认预测格式、patch 应用、日志、成本和
   官方容器闭环，再扩大运行。不要一开始直接跑五臂全部 30 题。

阶段 C：生成计划与预测

示例：

  python3 experiments/exp_fides_realfix_compare.py plan \
    --manifest experiments/realfix_30_manifest.json \
    --model <local-model-name> --scaffold <scaffold-version> --seed 1 \
    --token-budget 50000 --model-call-budget 12 \
    --output experiments/realfix_30_plan.json

  python3 experiments/exp_fides_realfix_compare.py validate \
    --manifest experiments/realfix_30_manifest.json \
    --predictions predictions/base.jsonl \
    --expected-model <local-model-name>

每一臂、每一 seed 使用独立预测文件。空 patch 必须保留并交给官方 harness
计为失败，不得删除或补成非空来美化结果。

阶段 D：官方评测

1. 对每个臂派生缓存安全的 run_id：

   RUN_ID=$(python3 experiments/exp_fides_realfix_compare.py run-id \
     --manifest experiments/realfix_30_manifest.json \
     --plan experiments/realfix_30_plan.json \
     --arm base --predictions predictions/base.jsonl)

2. 用该 RUN_ID 和 manifest 中 30 个 ID 调用官方 swebench eval verified。
   参考命令见 docs/fides_eval_v0_1.md。
3. patch 变化后必须重新派生 run_id，不能复用旧缓存。
4. 保存官方原始 results.json、逐实例 report、test output、预测文件和运行日志。
5. 不把本地 unittest smoke 写成官方结果，不把 incomplete/error 当成 resolved。

阶段 E：归一化与比较

对每臂运行 normalize，再运行 compare。检查模型、scaffold、seed、预算、任务
manifest 和预测哈希绑定。输出：
1. 每臂 30 题 resolved 数与比例；
2. dev/held-out 分别的 resolved；
3. completed、unresolved、error、empty patch、infra failure；
4. 每对臂的 only_left/only_right 任务；
5. 实际 token、模型调用、wall time、成本；
6. 失败案例和基础设施异常。

单 seed 只报告观察差异。至少多个 seed 后才讨论稳定性；样本只有 30 题时不得
夸大统计显著性。如 full archive 未获胜，也如实报告，这不否定治理机制本身。

六、工程纪律

1. 先写失败复现或回归测试，再修 bug。
2. 只改与 RealFix 直接相关的文件，不顺手重构、格式化无关代码。
3. 不通过注释测试、加 skip、放宽断言或关闭安全门来让流程通过。
4. 不提交 API key、token、密码、本地绝对路径或大型 Docker 镜像。
5. 模型和数据集版本、scaffold commit、命令、随机种子、预算必须可复现。
6. 每个新增功能配测试；运行相关测试、127 项治理回归、Ruff 和 git diff --check。
7. 不把计划、配置、合成 fixture、预测格式校验通过写成“已完成官方评测”。
8. 未经明确授权，不 push、不 merge、不删除分支、不修改远端。

七、交付物

1. 最小模型执行器及五臂策略实现；
2. 单元测试和至少 1 个 dev 任务的 pilot 证据；
3. 真实 realfix_30_manifest.json 和数据来源记录；
4. 每臂预测、实际资源消耗、官方原始结果和归一化结果；
5. 一份中文实验报告，区分已运行事实、合成测试、失败和未完成项；
6. 最终验证命令与结果；
7. 若被数据集、Docker、模型端点或资源阻塞，给出确切错误和已验证到的步骤，
   不生成虚构清单、预测或分数。

现在开始：先检查仓库和现有实现，运行相关测试，然后给出最小 pilot 计划。
```
