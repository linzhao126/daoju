# 刀具寿命预测 · 应采集的 FANUC FOCAS 参数清单（URL 版）

> 目的：基于 FANUC FOCAS 库在线文档，结合项目已证死的结论（现有 13 通道 = 负载百分比"影子量"，量化台阶导致磨损信号丢失），列出**可以采集、应该采集、且采集后有望预测刀具寿命**的指标。
> 生成时间：2026-09-21
> 依据文档：
> 
> - [cnc_loadtorq（异常负载扭矩 DTRQ）](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq)
> - [cnc_rdcurrent（伺服实际电流）](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent)
> - [cnc_rdsrvspeed（伺服实际转速）](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdsrvspeed)
> - [cnc_rdposerrz[s]（位置偏转，刚性攻丝专用）](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdposerrz) / [cnc_rdsynerrrg[y]（同步误差，刚性攻丝专用）](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdsynerrrg)
> - [cnc_diagnoss（诊断号读取，TCMD/ERR/IR/IS 等）](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)
> - [cnc_rdspload](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdspload) / [cnc_rdsvmeter](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdsvmeter) / [cnc_rdaxisdata](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdaxisdata)（现有"影子量"源头）
> - [cnc_modal（模态 G 代码）](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal)（空走/切削判别）
> - [cnc_rddynamic](https://www.inventcom.net/fanuc-focas-library/position/cnc_rddynamic) / [cnc_actf](https://www.inventcom.net/fanuc-focas-library/position/cnc_actf) / [cnc_acts](https://www.inventcom.net/fanuc-focas-library/position/cnc_acts) / [cnc_machine](https://www.inventcom.net/fanuc-focas-library/position/cnc_machine) / [cnc_distance](https://www.inventcom.net/fanuc-focas-library/position/cnc_distance)（动态数据/实际进给/主轴转速/坐标，组合判定）
> - [General / FWLIB30i 家族说明](https://www.inventcom.net/fanuc-focas-library/general/general)（机理与库归属依据）
> - `Fanuc内部高速数据与现有采集对比.md`（机理与专利依据）
> - [cnc_sdtsetchnl / cnc_sdtsetchnl2（CNC 内部高速采样 / PMC 采样）](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl)（[sdtsetchnl2](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl2)）— §2.6 来源
> - [cnc_rdpm_item（机床维护计时）](https://www.inventcom.net/fanuc-focas-library/misc/cnc_rdpm_item) — §2.6 来源

---

## 0. 一句话结论

**现有采集的 `L_spindle_load` / `L_X/Y/Z_servo_load` 是 FOCAS 的"负载表显示值"（整数百分比），是磨损力的"影子"，已被量化台阶吃掉。要预测寿命，必须改读 FOCAS 里另一族——电机**扭矩/电流**真身量：[`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq)（DTRQ 异常负载扭矩）、[`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent)（伺服实际电流）、以及 [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss) 诊断号里的 TCMD/ERR/IR/IS。这些量在库里**现成可用、且对车床（T 系列）全部开放**，不需要外接硬件。**

---

## 0.5 本机 Series 31i-MODEL B 的兼容性结论（关键）

经核对 FOCAS 库文档，**文档中所有指标均可在本机采集**，要点如下：

- **31i 在文档里被归并到 "30i" 列**：[General 页](https://www.inventcom.net/fanuc-focas-library/general/general) 明确 `FWLIB30i.DLL` = "Processing library of HSSB for **30i/31i/32i**"，30i/31i/32i 是同一 nanocontroller 家族、共用一套库。函数页矩阵只列 `30i-A`/`30i-B`，**Series 31i-MODEL B 对应 `30i-B` 这一列**。
- **三个核心函数 T（车床）行 × `30i-B` 列全部 = O（以太网+HSSB 都可用）**：
  - [`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq)（DTRQ 异常负载扭矩）：车床仅 **30i-B=O**（30i-A 对车床是 X）。→ 本机可用，且这正是工业专利方法的主信号。
  - [`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent)（伺服实际电流）：30i-A/B 均 O。→ 本机可用。
  - [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)（TCMD/ERR/IR/IS/IEFF/IQ/MTTMP 等诊断号）：30i-A/B 均 O。→ 本机可用。
- **DTRQ 是 30i/31i 专属高功能**：0i 及更早系列（0i-A/B/C/D/F、15、16/18/21、16i/18i/21i）在 `cnc_loadtorq` 的 T 行**全部为 X 不可读**。这纠正了本文初稿"0i 也能 O"的不准确说法；对 0i 机型只能退用 `cnc_rdcurrent`+TCMD（本文 §3 的兜底逻辑不变）。本机是 31i-B，无此限制。
- **REAL 诊断类型可用**：31i 属 30i 家族，支持 real 型诊断（连续工程值），故 TCMD/ERR 等有望以 **real 连续值**读出（优于 word 整数），具体类型须现场 `cnc_rddiaginfo` 枚举确认。
- **Tier 3 上下文函数**（负载% / 转速 / 位置）已是现盒子在读或最基础函数，本机确认可用，无需担心。
- **切削状态识别量也全可用、且零选项门槛**：[`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal)（模态 G 代码，Group 0 直接区分 G00 空走与 G01/G02/G03 切削）、[`cnc_rddynamic`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rddynamic)（一次读全 actf/acts/位置）、[`cnc_actf`](https://www.inventcom.net/fanuc-focas-library/position/cnc_actf)/[`cnc_acts`](https://www.inventcom.net/fanuc-focas-library/position/cnc_acts)/[`cnc_machine`](https://www.inventcom.net/fanuc-focas-library/position/cnc_machine)/[`cnc_distance`](https://www.inventcom.net/fanuc-focas-library/position/cnc_distance)，均属标准库，31i-B(T) 全部 = O，**无 license 依赖**。这是补齐"剔除空走刀"卡点的关键，详见 `切削状态识别_模态G组合判定.md`。

> 唯一仍需现场确认的是**选项/参数**：[`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq) 依赖"异常负载扭矩检测功能"（license 选项）；[`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent) 需参数 2086≠0 才有值（否则返 0）。这两件不是"协议能否调通"，是"机床买没买这个选项 / 设没设参数"，须现场查。

---

## 1. 当前已采集的（必须认清：这是"影子"，不是"真身"）

这些是盒子现在已经在读的，对应 FOCAS 的 **Position 分类 "meter" 家族**：

| 你现在有的列                  | FOCAS 函数                                                                                | 返回结构                                           | 物理含义          | 死穴                                    |
| ----------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------- | ------------------------------------- |
| `L_spindle_load`（主轴负载%） | [`cnc_rdspload`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdspload)   | `ODBSPN.data[0]`                               | 串行主轴**负载百分比** | 整数 %，无量化台阶则被平滑                        |
| `L_X/Y/Z_servo_load`    | [`cnc_rdsvmeter`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdsvmeter) | `ODBSVLOAD.svload.data`（含 `dec`/`unit`/`name`） | 伺服轴**负载百分比**  | X≈180 级、Y≈60 级、**Z 实测只剩 {3,4,5} 三台阶** |
| `L_spindle_load`（另一来源）  | [`cnc_rdspmeter`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdspmeter) | 同 spload 家族                                    | 主轴负载表         | 同 spload                              |

**判读**：这族函数读的就是给操作工看的"负载表 %"。实测 Z 轴 96.4% 的行只取 {3,4,5}，磨损引起的力变化（百分之几）比一个台阶还小，**在"取整成百分比"那一步就丢了**。它们不是预测特征，只能当校准/对照用，绝不能再当建模输入。

---

## 2. 应该采集的（真身量，按价值分层）

### 🥇 Tier 1 — 必须采集（磨损力的真身，直接对应工业成功案例）

| #   | 指标                      | FOCAS 函数                                                                                            | 分类    | 返回 / 量纲                                                  | 为什么能预测磨损                                                 | 选项要求                                     | 车床(T)可用                            |
| --- | ----------------------- | --------------------------------------------------------------------------------------------------- | ----- | -------------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------- | ---------------------------------- |
| 1   | **DTRQ 异常负载扭矩（主轴）**     | [`cnc_loadtorq(motor=1, …)`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq)      | Motor | `data∈[−32767,+32767]`；%最大扭矩 = data/32767×100；ODBLOAD    | **FANUC 自家专利 US5602347 就是用扰动扭矩判寿命**；主轴扭矩 = 车削主切削力（切向）的真身 | 需"异常负载扭矩检测"功能；嵌入式以太网下 extended driver 不要 | 0i-B/C/D/F、30i-A/B = **O**（可用）     |
| 2   | **DTRQ 异常负载扭矩（伺服 X/Z）** | [`cnc_loadtorq(motor=0, axis=…)`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq) | Motor | 数字负载电流 `0~±7282`；实际电流 = data×N/7282 [Apeak]，N=参数2165     | 伺服轴吃径向/轴向分力，磨损→分力升→电流升，连续高分辨                             | 同上                                       | 同上 = **O**                         |
| 3   | **伺服实际电流（X/Y/Z）**       | [`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent)                | Motor | `short crrnt[MAX_AXIS]`（每轴实际电流）                          | **工业"电流法"标准信号**（台湾中兴大学、HUAYI、iFactory 都用电机电流）；电流随刀具钝化单调升 | 基本无额外选项（参数2086≠0 才有值，否则返0）               | 全 0i/30i/16iB/18iB/21iB = **O**    |
| 4   | **TCMD 扭矩指令**           | [`cnc_diagnoss(number=诊断号, axis)`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss) | Misc  | 诊断号（word 型，维修手册载 −6554~6554 归一化到最大电流；31i 家族可支持 real 连续值） | 反映 CNC 输出给电机的扭矩指令；磨损→为维持切削转速需更大扭矩→TCMD 升                 | 通常不需额外选项（指令是控制量）                         | 0i-B/C/D/F、30i、31i-MODEL B = **O** |

> **T1111 是车床的关键提醒**：车削主切削力是**切向力**，**落在旋转主轴**上、由**主轴扭矩**承载；直线轴 X（径向）/Z（轴向）只吃分力。所以**主轴侧 #1（DTRQ）是首选主信号**，#2/#3/#4 是辅助/交叉验证。这和项目之前"控制回路控转速非扭矩"的判定一致——主轴扭矩正是最该被读的维度。

### 🥈 Tier 2 — 应该顺带采（次级 / 交叉验证 / 冗余）

| #   | 指标                    | FOCAS 函数                                                                                               | 分类   | 量纲           | 用途                                       | 选项要求   |
| --- | --------------------- | ------------------------------------------------------------------------------------------------------ | ---- | ------------ | ---------------------------------------- | ------ |
| 5   | **ERR 位置偏差（跟随误差）**    | [`cnc_diagnoss(number=ERR诊断号, axis)`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss) | Misc | 稳态位置偏差 ∝ 负载力 | 间接反映切削阻力；几乎任何系列可读、常不需选项，且仅匀速段有效          | 视诊断号而定 |
| 6   | **IR / IS 电机两相电流**    | [`cnc_diagnoss(number=IR/IS诊断号)`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)     | Misc | 电流原始值        | 与 #3 互为冗余，若 `cnc_rdcurrent` 返0（参数未设）时的替代 | 视诊断号而定 |
| 7   | **IEFF / IQ 有效/有功电流** | [`cnc_diagnoss(诊断号)`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)                 | Misc | 电流分量         | 区分有功（切削）与无功分量，比总电流更贴磨损                   | 视诊断号而定 |
| 8   | **MTTMP 电机绕组温度**      | [`cnc_diagnoss(诊断号)`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)                 | Misc | ℃（分辨率1）      | 排除温升对电流的干扰（温控→电流基线漂移）                    | 视诊断号而定 |

### 🥉 Tier 3 — 保留作上下文（不是预测特征，但有用）

| #   | 指标                       | FOCAS 函数                                                                                                                                                                                                      | 用途                                                                             | 说明              |
| --- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | --------------- |
| 9   | 主轴/伺服负载%（现有）             | [`cnc_rdspload`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdspload) / [`cnc_rdsvmeter`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdsvmeter)                               | 与 Tier1 对照、验证"改读后信号确实更细"                                                       | 留着当基线，不进模型      |
| 10  | 实际转速 / 进给 / 位置 / **模态G** | [`cnc_rdaxisdata`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdaxisdata) / `cnc_rddynamic` / `cnc_rdspeed` / **[`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal)** | 切分调用段、识别空走刀（主轴转但 `cnc_rdcurrent≈0`）、做工况归一化；**模态G 直接区分 G00 空走与 G01 切削，详见 §2.5** | 运动学上下文，必备但不直接预测 |
| 11  | 伺服实际转速                   | [`cnc_rdsrvspeed`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdsrvspeed)                                                                                                                        | 与 #10 冗余，确认恒定转速工况                                                              | 上下文             |

> **刚性攻丝专用（T1111 大概率用不上，列出以防误采）**：[`cnc_rdposerrz[s]`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdposerrz)（位置偏转）、[`cnc_rdsynerrrg[y]`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdsynerrrg)（同步误差）——文档明确写"仅在刚性攻丝/同步控制模式有效"，普通车削不触发，**不要为预测磨损去采这两个**。

---

## 2.5 切削段识别专用参数（空走刀过滤 · 数据清洗前置 · 零选项门槛 · 最该先落地）

现有 `L_state==3` 只能说明"机床在运行"，**不能区分空走刀（G00）与真切削（G01/G02/G03）**。要把空气切削段剔除、只留真切削段做磨损特征，必须加读以下量。其中 **[`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal)（模态 G 代码）是最直接、零选项的判定量**，比 13 通道负载%科学得多（完整分析见 `切削状态识别_模态G组合判定.md`）。

| #   | 指标                        | FOCAS 函数                                                                                                                                                                    | 分类       | 返回/量纲                                                     | 在判定中的角色                          | 选项要求                            | 车床(T)可用          |
| --- | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | --------------------------------------------------------- | -------------------------------- | ------------------------------- | ---------------- |
| C1  | **模态 G 代码（Group 0 运动模态）** | [`cnc_modal(type=-1, block=0)`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal) → `g_rdata[0]`                                                                | Misc     | `g_rdata[0]`: 0=G00, 1=G01, 2=G02, 3=G03 …                | **直接判别**：G00=空走；G01/G02/G03=进给切削 | 无（标准库，30i 家族 type=-1 一次读全 21 组） | 30i/31i = **O**  |
| C2  | **动态数据一次读全**              | [`cnc_rddynamic(axis=-1, …)`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rddynamic)                                                                         | Position | `actf`(实际进给) `acts`(主轴转速) 各轴 `machine`/`distance` `alarm` | 组合判定：进给>0、主轴转、轴在动                | 无                               | 30i-B(T) = **O** |
| C3  | 实际进给速度                    | [`cnc_actf`](https://www.inventcom.net/fanuc-focas-library/position/cnc_actf)                                                                                               | Position | `ODBACT.data`（如 G1 切削段 F=1200）                            | `actf>0` 佐证在进给                   | 无                               | O                |
| C4  | 实际主轴转速                    | [`cnc_acts`](https://www.inventcom.net/fanuc-focas-library/position/cnc_acts)                                                                                               | Position | 主轴实际转速                                                    | 主轴旋转佐证                           | 无                               | O                |
| C5  | 机床坐标 / 剩余移动量              | [`cnc_machine`](https://www.inventcom.net/fanuc-focas-library/position/cnc_machine) / [`cnc_distance`](https://www.inventcom.net/fanuc-focas-library/position/cnc_distance) | Position | 各轴位置值                                                     | 判轴是否真在动（相邻行差值>0）                 | 无                               | O                |

**判定逻辑（每条采样行）：**

```
cutting = (modal_G0 in {G01,G02,G03,G32..})   # 进给模态
          AND actf > 0                          # 实际在进给
          AND acts > spindle_min                 # 主轴真在转
          AND axis_moving(machine/distance 差值) # 轴真在动
air_cut = (modal_G0 == G00)                     # 空走，剔除
```

> 边界：逼近/退刀未接触（G01 但未吃刀）需 P0 转矩通道二次确认；标准 FOCAS 无官方 "CUTTING_NOW=1" 布尔量，模态 G 是最贴近语义的指标。

---

## 2.6 更深层的补充通道（cnc_sdt* 高速采样 / cnc_rdpm_item 维护计时 / PMC sdt2 采样）· 已逐条核实 · 含误读修正

> 来源：有同行把 FOCAS 库按"深度阶梯"挖到更深一层（负载% → 控制环工程量 → 诊断号 → **CNC 内部高速采样 cnc_sdt\*** → **PMC 梯形图采样 sdt2** → **机床本体寿命计数 cnc_rdpm_item** → 补偿/误差表）。下面**只收经 FOCAS 库在线文档逐条核对后确认属实、且对本项目有用**的部分，并把其中几处夸大/误读明确标出，避免照单全收。

### 2.6.1 三档定位（先分清"它是干什么用的"）

| 档位 | 通道 | 用途 | 是否进生产管线 |
|---|---|---|---|
| A 生产连续监控 | §2 的 DTRQ/TCMD/电流/ERR（改地址轮询） | 长期连续采集做磨损特征 | ✅ 是（主路线） |
| B 离线高分辨标定 | [cnc_sdtsetchnl](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl) 0.25ms/1ms 追踪采样 | §4 标定实验：新刀/中段/临换刀各抓一组 4kHz 波形 | ❌ 否，调试/标定利器 |
| C 机床级参考 | [cnc_rdpm_item](https://www.inventcom.net/fanuc-focas-library/misc/cnc_rdpm_item) 维护计时 | 机床厂家写的导轨/丝杠/主轴分钟级寿命计时 | ❌ 否，交叉参照 |

> **关键澄清**：`cnc_sdt*` 是**追踪/调试（armed trace）**函数（文档明写"不能与 Servo Guide 同时用 EW_REJECT"，有 start/end/cancel 状态机，缓冲上限除非 serial read 设 -1）。它**不是**能像 `cnc_rdsvmeter` 那样生产中无限轮询的 telemetry。所以 **A 档"生产监控改地址读 DTRQ/TCMD"的结论仍然成立**，sdt 是标定手段而非替代管线。

### 2.6.2 已核实、且有用（真东西）

| 量 | 接口 / type | 31i-B(T) | 用处 | 注意 |
|---|---|---|---|---|
| 高速采样 DTRQ/TCMD/电流/MTTMP（带官方定标） | [cnc_sdtsetchnl](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl) + sdtreadsmpl，伺服 0.25ms / 主轴 1ms，最多 8 通道 | O（Embedded Ethernet 免选项，仅扩展驱动） | **离线标定捕获**：一次拿全、带定标（伺服 DTRQ 7282=最大电流、主轴 DTRQ 16384=最大扭矩、MTTMP 1=℃），正好是 §4 标定实验的"高分辨快照" | 是追踪函数，非连续管线 |
| **SMTTRQ** 智能反隙估算负载扭矩（type 43） | 同上 sdt 通道 | O | 新的候选磨损信号：智能反隙补偿里的估算负载扭矩 | 值得一试，但属 sdt 离线条带 |
| **aiGS 内置加速度** 振动族（type 34–37，FREQ/FRTCM/ACC） | 同上 sdt 通道 | O（**前提是机床装了 aiGS 模块 + 参数 2277#7,6,5 指定方向**） | 若现场有，免费振动源（不用外接加速度计） | ⚠️ sdt 伺服最快 0.25ms=**4kHz**、主轴 1kHz，**低于**原分析说的 10kHz，未达振动/AE 门槛；是否够用需受控标定验证 |
| **cnc_rdpm_item** 机床维护计时 | [cnc_rdpm_item](https://www.inventcom.net/fanuc-focas-library/misc/cnc_rdpm_item) struct `IODBPMAINTE`（name/type/total/remain/stat），type=4 Cutting 按切削时间累计，单位=分钟 | O（cnc_rdpm_mcnitem O；cnc_rdpm_cncitem X） | 机床级维护参照：导轨/丝杠/主轴分钟寿命、剩余时间 | ⚠️ 是**机床级**维护计时，**不是单把刀寿命**，不能替代换刀记录；只能作交叉参考 |
| sdt2 采 PMC（切削检测器/换刀位） | [cnc_sdtsetchnl2](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl2) axis=-125/-126/-127 | O（30i-B 起支持） | 若梯形图暴露切削检测位/换刀动作位，可做**毫秒级事件对齐**（比靠 T 码轮询推断更干净） | PMC 是 **4ms 网格**，非 0.25ms；且需梯形图确实暴露该信号（现场确认） |

### 2.6.3 误读修正（必须纠正，别照原帖接）

1. **BLCMP / SMTBKL（反向间隙补偿量，type 39/42）≠ 机械磨损"直读"**。文档里它是 `Backlash compensation`——CNC **施加的补偿值（控制输出/参数）**，单位 `1=检测单位`。CNC **不会因为丝杠磨损就自动把 BLCMP 调大**，那是人或智能补偿算法设定的。所以"BLCMP↑=丝杠磨损↑"不成立，受参数/整定强烈混淆，**不是传感器、不能当磨损判据**。原帖那张"三曲线分离表（刀具磨损/TCMD↑、机械磨损/BLCMP↑、热漂移/MTTMP↑）"的 BLCMP 行**错误**，整张表因此站不住。→ 本清单**不把 BLCMP 列为磨损量**，只当补偿量上下文（若现场开了智能反隙，可作干扰项观察）。
2. **EPMTR（主轴功耗 0.01kW，type 9）可用性被夸大**。文档写明需"功率监控选项（No.4541#0=1）"，且"type=9 在 9D80/T(20) 或以后才有效（plan）"。31i-B 上 EPMTR **很可能不可用或要选项**，不是免费午餐。→ 列为"待现场确认选项"，不默认可用。
3. **rdvolccomp / rdpitchr 被拔高为"直读"**。volccomp 是 CNC 实时施加的误差补偿（几何+热），反映热态但是"修正量"非标定温度；rdpitchr 是螺距误差补偿表，标定/维护时才改，不记连续磨损。二者都是 proxy/校准量，不能说"热变形直读""磨损时间线"。
4. **sdt 集成成本被低估**：sdt 是整套不同 API（setchnl→startsmpl→readsmpl→endsmpl + 缓冲 + EW_REJECT 状态机），比 `cnc_diagnoss` 读个诊断号复杂得多；**采集盒厂商是否已封装这套 API 是未知数**（现场确认点之一）。
5. **重复强调**：这些更深通道**解决的是"量化台阶/分辨率"**，但项目 RUL 证死的两大死穴之一是**没有真实 VB 标签**——sdt 一字未提、也解决不了。所以 sdt **不会让"精确预测剩余寿命%"复活**，只提升同一套"状态监测/指纹报警/Weibull 换刀"交付物的信噪比。

### 2.6.4 给供应商/现场的四项确认（与新通道绑定）

| # | 确认项 | 为什么 |
|---|---|---|
| 1 | **EPMTR 功率监控选项**（No.4541#0）是否开启、31i-B 是否支持 type=9 | 决定 §2.6.2 功率法能否用 |
| 2 | **aiGS 模块**（内置加速度）是否装配 + 参数 2277#7,6,5 | 决定免费振动源是否存在（即便有也只有 4kHz） |
| 3 | **PMC 梯形图**是否暴露切削检测器位 / 换刀动作位（X/Y/G/F 区具体地址） | 决定 sdt2 事件对齐能否落地 |
| 4 | **cnc_sdt* / sdt2 是否已被采集盒 SDK 封装** | 决定我们能否直接用，还是得写新采集程序 |

---

## 3. 车床（T1111）应采集的"最小高价值组合"

落地优先级（按"成本最低 + 信号最直接"）：

**第 0 步（最先做、零门槛）：切削段识别** — 先加 [`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal) + [`cnc_rddynamic`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rddynamic)（或 [`cnc_actf`](https://www.inventcom.net/fanuc-focas-library/position/cnc_actf)/[`cnc_acts`](https://www.inventcom.net/fanuc-focas-library/position/cnc_acts)/[`cnc_machine`](https://www.inventcom.net/fanuc-focas-library/position/cnc_machine)），把空走刀(G00)与真切削(G01+)分开、生成 `cutting` 标签。这是所有磨损特征的前置清洗，详见 `切削状态识别_模态G组合判定.md`。

1. **主轴 DTRQ**（[`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq), motor=1）— 主信号，对应专利方法，车削切向力真身。
2. **X/Z 伺服实际电流**（[`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent)）— 工业电流法，零额外选项门槛，作交叉验证。
3. **TCMD 扭矩指令**（[`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss) 诊断号）— 控制量，几乎无门槛，作冗余。
4. **ERR 位置偏差**（[`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss) 诊断号）— 间接阻力，仅匀速段有效。

**若"异常负载扭矩检测"选项买不到**（[`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq) 返 `EW_NOOPT`=6）：退而用 **#3 `cnc_rdcurrent` + #4 TCMD**——它们不依赖该选项，仍能拿到连续扭矩/电流维度，只是少了 DTRQ 这个"已扣摩擦"的干净量。

---

## 4. 落地：让供应商/采集盒改读的步骤

> 核心动作只有一件：**把采集函数从 Position 的 [`cnc_rdspload`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdspload)/[`cnc_rdsvmeter`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdsvmeter)（读 %）换成 Motor 的 [`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq)/[`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent) 和 Misc 的 [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)（读诊断号）**。同一根以太网（8193），改配置即可，不动硬件。

| 步骤  | 动作                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | 产出                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| 1   | 现场确认 CNC 系列（0i-TD / 0i-TF / 30i-TB 等）与是否 Embedded Ethernet                                                                                                                                                                                                                                                                                                                                                                                                           | 决定选项是否免除               |
| 2   | 跑 `cnc_rddiaginfo` 枚举本机诊断号表，定位 TCMD / ERR / IR/IS / IEFF / IQ / MTTMP 对应的 `number` 与 `type`（word/real）                                                                                                                                                                                                                                                                                                                                                               | 诊断号清单（每台机型不同）          |
| 3   | 改采集盒调 [`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal)（模态G，空走/切削标签）+ [`cnc_rddynamic`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rddynamic)（actf/acts/位置）+ [`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq) + [`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent) + [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss) | 新采集程序（模态G与动态量零选项，最先上线） |
| 4   | **一次性标定实验**：新刀 vs 寿命中段 vs 临换刀，各抓一组，看 DTRQ / 电流是否出现 **≥10% 且方向一致** 的稳态偏移（同时验证 [`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal) 的 G00/G01 标签与现场 NC 程序段一致）                                                                                                                                                                                                                                                                                   | 信号有效性判据（防止自欺）          |
| 5   | 有效 → 长期连续采集（FOCAS/SDK 或 MT-LINKi）；无效 → 走 `P0失败降级路线.md` 的 B 层（工件质检数字化）                                                                                                                                                                                                                                                                                                                                                                                                | 决策闭环                   |

---

## 5. 诚实边界（不回避风险）

1. **选项可能缺失**：[`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq)（DTRQ）需"异常负载扭矩检测功能"。若没有，按 §3 退用 [`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent)+TCMD（已确认不依赖该选项）。
2. **控制回路极紧仍是已知风险**：本项目加工高度标准化（转速差0.5%、负载差2%），磨损信号即便在扭矩维度也可能被部分吸收。**必须用 §4 第4步的标定实验实测，不能靠推理**。
3. **诊断号随系列变**：TCMD/ERR 等的 `number` 不在 FOCAS 库里列死，必须现场 `cnc_rddiaginfo` 枚举，不能凭型号猜。
4. **频率不变 ≠ 信号不变**：这些 Motor/Misc 函数仍走轮询（当前~16ms），**分辨率突破来自"读原始扭矩/电流值"而非"采更快"**。若需极高频（16kHz），才用 Servo Guide 内录（调试工具，非生产）。
5. **标签仍是另一道关**：即便扭矩信号完美，本项目仍**没有真实 VB 标签**（人工换刀只给"事件"）。预测要么走"阈值/状态监测"（工业成熟路线，对应已交付的 `alarm_online.py`），要么补 VB（便携显微镜，非传感器）或工件质检数字化。详见 `P0失败降级路线.md`。

---

## 6. 采样频率两档分配（4Hz / 1Hz）· 给供应商的频率指令

> 结论：本清单**没有任何量需要 kHz 高频**。磨损是慢变量（上百次调用才废），卡脖子的是"读哪个地址（分辨率）"不是"采多快"。分档只看一条：**该量要不要算稳态均值、剔加减速惯性尖峰**——要则放高频档，不要（离散语义态/分钟级慢变）则放低频档。

### 6.1 放 4Hz（1秒4次）的指标 —— 扭矩/电流家族（磨损+热信息的唯一承载）

| 编号  | 指标              | FOCAS 函数                                                                                   | 为什么必须这档                                         |
| --- | --------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| #1  | DTRQ 异常负载扭矩（主轴） | [`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq) motor=1 | 磨损主信号（专利 US5602347），每刀~30s 需足够点算稳健稳态均值、剔段边界惯性尖峰 |
| #2  | DTRQ（伺服 X/Z）    | [`cnc_loadtorq`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_loadtorq) motor=0 | 同上，径向/轴向分力                                      |
| #3  | 伺服实际电流（X/Y/Z）   | [`cnc_rdcurrent`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdcurrent)       | 工业电流法真身，同需高频                                    |
| #4  | TCMD 扭矩指令       | [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)          | 控制量冗余，承载磨损趋势                                    |
| #5  | ERR 位置偏差        | [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)          | 间接阻力，仅匀速段有效，稳态段取均值需点数                           |
| #6  | IR / IS 两相电流    | [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)          | 电流冗余家族                                          |
| #7  | IEFF / IQ 电流分量  | [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)          | 电流分量家族                                          |
| SMTTRQ | 智能反隙估算负载扭矩（type 43） | [`cnc_sdtsetchnl`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl) sdt 通道 | 扭矩家族新候选（智能反隙补偿里的估算负载），承载磨损趋势，逻辑同 DTRQ 家族；可 4Hz 连续读或随 sdt 离线抓（§2.6.2） |
| aiGS 加速度 | 内置振动（type 34–37：FREQ/FRTCM/ACC） | [`cnc_sdtsetchnl`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl) sdt 通道 | ⚠️ **不适用 4Hz 档**：源本身 4kHz（伺服 0.25ms），远超本两档；不进连续 4Hz 轮询，仅离线 sdt 快照或独立高频通道，且需机床装 aiGS 模块 + 参数 2277#7,6,5（§2.6.2） |

### 6.2 放 1Hz（1秒1次）的指标 —— 慢变/语义态（无损）

| 编号  | 指标             | FOCAS 函数                                                                                                                                                                                                  | 为什么 1Hz 够                                |
| --- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| #8  | MTTMP 绕组温度     | [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss)                                                                                                                         | 温升分钟~数十分钟级慢变，℃ 分辨率，1Hz 已是其上限（理想 0.1–1Hz） |
| #9  | 主轴/伺服负载%（现有）   | [`cnc_rdspload`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdspload) / [`cnc_rdsvmeter`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdsvmeter)                           | 只当对照基线，慢变语义                              |
| #10 | 实际转速/进给/位置/模态G | [`cnc_rdaxisdata`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rdaxisdata) / `cnc_rddynamic` / `cnc_rdspeed` / [`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal) | 离散语义态，只判"在不在切"，1Hz 甚至更粗都行                |
| #11 | 伺服实际转速         | [`cnc_rdsrvspeed`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_rdsrvspeed)                                                                                                                    | 上下文冗余                                    |
| C1  | 模态 G 代码        | [`cnc_modal`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_modal)                                                                                                                               | 分段语义，per-block 即可                        |
| C2  | 动态量一次读全        | [`cnc_rddynamic`](https://www.inventcom.net/fanuc-focas-library/position/cnc_rddynamic)                                                                                                                   | 组合分段，语义态                                 |
| EP  | EPMTR 主轴功耗（type 9） | [`cnc_sdtsetchnl`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl) sdt 通道 / [`cnc_diagnoss`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_diagnoss) | 功率 = 扭矩×转速，磨损时上升；慢变量，1Hz 够。**⚠️ 待确认**：需功率监控选项 No.4541#0 且 31i-B 未必支持 type=9（§2.6.3） |
| PM  | cnc_rdpm_item 机床维护计时 | [`cnc_rdpm_item`](https://www.inventcom.net/fanuc-focas-library/misc/cnc_rdpm_item)                                                                                                                    | **非连续轮询**：机床级维护计时（导轨/丝杠/主轴分钟寿命、剩余时间），按班/日周期读即可，不占连续 1Hz 带宽；是机床级参考、非单把刀寿命（§2.6.2） |
| PMC | sdt2 采 PMC（切削检测器/换刀位） | [`cnc_sdtsetchnl2`](https://www.inventcom.net/fanuc-focas-library/motor/cnc_sdtsetchnl2) axis=-125/-126/-127                                                                                          | **事件信号、非连续轮询**：若梯形图暴露切削检测/换刀动作位，做毫秒级事件对齐（PMC 4ms 网格）；按事件触发采，不占 1Hz 连续带宽（§2.6.2） |

### 6.3 关键边界（防自欺）

1. **扭矩家族（#1–#7 + SMTTRQ）理想 5–10Hz+（现 16ms=62.5Hz 已绰绰有余）**；4Hz 是它们能接受的**下限**。若采集盒上限就是 4Hz，放 4Hz 没问题；但**不要降到 1Hz**——每刀仅 ~30 点，剔段边界惯性尖峰后有效样本太少，稳态均值会抖。两档分配里 #1–#7 + SMTTRQ 必须咬死 4Hz。
2. **#1–#7 + SMTTRQ 在 1Hz 技术上能采、但不推荐**：不是不能采，是太稀。其余**连续**量（#8–#11、C1–C2、EP）在 1Hz 完全无损（尤其 MTTMP，1Hz 对它都偏密）。**aiGS 加速度不在此列**——源 4kHz，远超两档，只能离线 sdt 或独立高频，不进 4Hz/1Hz 连续轮询。**PM（rdpm_item）与 PMC（sdt2）非连续轮询**（周期读 / 事件触发），不占连续带宽。
3. **降频收益**：上下文/温度从 16ms 降到 1Hz，数据量约为原 1/16~1/60，**总数据量可砍掉一大半而不丢任何信息**。与 §2.5 切削段识别、§5 第4点（频率不变≠信号不变）一致——瓶颈在"读哪个地址"，不在"采多快"。

### 6.4 给供应商的一行指令

> DTRQ / TCMD / ERR / 电流类（#1–#7）+ **SMTTRQ（type43）** 保持 **4Hz**（盒上限允许则更高）；模态G / 进给 / 主轴转速 / 位置 / 负载% / 伺服转速（#8–#11、C1–C2）降到 **1Hz**；MTTMP（#8）可 0.1–1Hz；**EPMTR（type9）待确认选项后按 1Hz**（功率慢变量）。**aiGS 加速度（type34–37）不进连续两档**——仅离线 sdt 快照或独立高频通道。**cnc_rdpm_item（机床维护计时）按班/日周期读、sdt2 PMC（切削检测/换刀位）按事件触发**，均非连续轮询。全部走同一根以太网（8193），频率是采集盒轮询配置项，调配置即可，不动硬件。

---

## 附：本清单与项目其他文档的关系

- `Fanuc内部高速数据与现有采集对比.md` — 机理与专利依据（本清单的"为什么"）。
- `采集链路与CNC内部参数说明.md` — 采集架构（盒子→以太网→FOCAS→CNC）。
- `工业案例与本项目的差异分析.md` — 别人为什么行（读电流/扭矩）、我们为什么不行（读 %）。
- `P0失败降级路线.md` — 若这些扭矩量也读不到时的应急方案。
- `传感器不敏感_详细分析报告.md` — 现有 13 通道无信号的证据。
- `切削状态识别_模态G组合判定.md` — 空走刀/切削段识别（模态 G 组合判定），数据清洗前置、零选项门槛。
- `热机摩擦力漂移_热磨损解耦方案.md` — 热漂移与磨损解耦方案；§2.6 的 sdt 离线标定 / 温度量 / rdpm_item 是其输入信号源。
- §2.6（深层补充通道：cnc_sdt* 离线高分辨 / cnc_rdpm_item 机床级计时 / PMC sdt2 事件对齐）— 经 FOCAS 库逐条核实、含 BLCMP/EPMTR 误读修正。
