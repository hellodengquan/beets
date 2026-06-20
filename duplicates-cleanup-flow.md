# 重复条目清理工作流指南

> 基于 `beetsplug/duplicates.py` 插件代码分析整理，用于离线复核与操作参考。

---

## 一、分组规则详解

### 1.1 核心分组算法

分组逻辑由 `_group_by()` 方法实现（`beetsplug/duplicates.py:280-308`），其核心机制是：

- 对每条音乐记录提取指定字段的值
- 过滤掉 `None` 和空字符串
- 将非空值组成元组作为分组键
- 按键进行分桶，同组内条目数 > 1 即为重复

### 1.2 默认分组键

| 模式 | 默认键 | 说明 |
|------|--------|------|
| 单曲模式 | `mb_trackid`, `mb_albumid` | 基于 MusicBrainz 曲目 ID + 专辑 ID 双重匹配 |
| 专辑模式 | `mb_albumid` | 基于 MusicBrainz 专辑 ID 匹配 |

### 1.3 自定义分组键

通过 `-k/--key` 参数可指定任意字段组合进行匹配：

```bash
# 按标题 + 艺术家 + 专辑匹配
beet dup -k title -k albumartist -k album

# 按音频长度 + 比特率匹配
beet dup -k length -k bitrate
```

### 1.4 严格模式 vs 非严格模式

| 模式 | 触发条件 | 行为 | 适用场景 |
|------|---------|------|---------|
| **非严格**（默认） | `--strict` 未设置 | 只要有至少一个键有值，就参与分组 | 元数据不完整的库，尽量多发现重复 |
| **严格模式** | `--strict` | 所有指定键都必须有值，才参与分组 | 元数据质量高，避免误匹配 |

> **代码依据**：`duplicates.py:292-303`
> - 严格模式：`len(values) < len(keys)` 时跳过
> - 非严格模式：`len(values) == 0` 时才跳过

### 1.5 校验和分组模式

通过 `--checksum PROG` 可基于音频内容计算校验和进行分组：

```bash
# 基于 CRC32 校验
beet dup -C 'ffmpeg -i {file} -f crc -'

# 基于 MD5 校验
beet dup -C 'md5sum {file}'
```

**特性**：
- 首次运行会计算并将结果缓存为 flexattr（数据库弹性属性）
- 后续运行可直接使用缓存值，无需重复计算
- 校验和键名为程序名（如 `md5sum`）

> **代码依据**：`_checksum()` 方法（`duplicates.py:247-278`）

---

## 二、优先级排序机制

### 2.1 排序目的

排序决定了在一组重复条目中，**哪一条作为"保留项"，其余作为"重复项"**。默认报告只显示重复项（非完整模式），排序靠后的条目会被标记为待清理。

### 2.2 默认排序规则

由 `_order()` 方法实现（`duplicates.py:310-345`）：

| 对象类型 | 排序依据 | 优先级方向 |
|---------|---------|-----------|
| **单曲 (Item)** | 元数据完整性（非空字段数量） | 字段越多越优先 |
| **专辑 (Album)** | 包含的曲目数量 | 曲目越多越优先 |

**单曲完整性计算细节**（`duplicates.py:328-339`）：

```python
def truthy(v):
    # 避免 bytes 与 Unicode 空字符串比较产生警告
    return v is not None and (v != "" if isinstance(v, str) else True)

# 统计所有 Item 字段中非空值的数量
def key(x):
    return sum(1 for f in Item.all_keys() if truthy(getattr(x, f)))
```

### 2.3 自定义排序规则

通过 `tiebreak` 配置项可自定义排序字段，支持按对象类型分别配置：

```yaml
duplicates:
  tiebreak:
    items: [bitrate, length]   # 单曲：高码率、长时长优先
    albums: [year]              # 专辑：发行年份优先
```

**配置示例对应的排序效果**：
- 先按 `bitrate` 降序，码率高的优先保留
- 码率相同时按 `length` 降序，时长长的优先保留
- 使用 `reverse=True`，即值越大优先级越高

> **代码依据**：`duplicates.py:321-324` — `tiebreak` 配置下，使用 `tuple(getattr(x, k) for k in tiebreak[kind])` 作为排序键

### 2.4 排序与报告输出的关系

```python
# _duplicates() 方法中的关键逻辑
offset = 0 if full else 1
# ...
objs = self._order(objs, tiebreak)  # 排序
yield (k, len(objs) - offset, objs[offset:])
```

| 模式 | offset | 输出内容 |
|------|--------|---------|
| 默认模式 | 1 | 跳过排序第一的条目，输出其余（即重复项） |
| `--full` 完整模式 | 0 | 输出所有重复条目（含保留项） |

---

## 三、清理动作分级详解

### 3.1 动作总览

清理动作从可逆到不可逆分为以下几个等级：

```
低风险 ←——————————————————————————————→ 高风险
标记 → 复制 → 移动 → 仅移除库 → 删除
  ↓      ↓      ↓       ↓         ↓
  可    可     部分     部分     不可
  逆    逆      可逆      可逆      逆
```

### 3.2 各级动作详解

#### 等级 0：纯报告（无动作）

**命令**：`beet dup`（默认行为）

**效果**：
- 仅在终端输出重复条目列表
- 不修改数据库，不修改磁盘文件
- 完全安全，可反复运行

**代码路径**：`_process_item()` 中仅执行 `print_()` 语句（`duplicates.py:228`）

---

#### 等级 1：打标签 (`--tag`)

**命令**：`beet dup --tag dup=1`

**效果**：
- 为重复项添加自定义 flexattr 属性
- 数据库写入操作，但不影响文件本身
- 可通过后续查询筛选已标记条目

**代码实现**（`duplicates.py:239-245`）：

```python
if tag:
    try:
        k, v = tag.split("=")
    except Exception:
        raise UserError(f"{PLUGIN}: can't parse k=v tag: {tag}")
    setattr(item, k, v)
    item.store()  # 写入数据库
```

**撤销路径**：
- 方法一：使用 `modify` 命令清除标签
  ```bash
  beet modify dup:1 dup=
  ```
- 方法二：重新导入覆盖
- **风险等级**：极低，纯元数据操作

---

#### 等级 2：复制 (`--copy`)

**命令**：`beet dup --copy /path/to/backup/`

**效果**：
- 将重复项复制到目标目录
- 原文件保留在原位
- 数据库中路径不更新（复制的是独立文件）

**代码实现**（`duplicates.py:229-231`）：

```python
if copy:
    item.move(basedir=copy, operation=MoveOperation.COPY)
    item.store()
```

**底层机制**（`library/models.py:1090-1143`）：
- 使用 `MoveOperation.COPY` 模式
- 调用 `util.copy()` 复制文件
- 源目录不会被清理（`prune_dirs` 仅在 MOVE 模式调用）

**撤销路径**：
- 直接删除复制过去的文件即可
- 原库不受任何影响
- **风险等级**：低，原文件完好无损

---

#### 等级 3：移动 (`--move`)

**命令**：`beet dup --move /path/to/trash/`

**效果**：
- 将重复项移动到目标目录
- 数据库中路径同步更新
- 原位置文件被移走，目录被自动清理（如果变空）

**代码实现**（`duplicates.py:232-234`）：

```python
if move:
    item.move(basedir=move)
    item.store()
```

**底层机制**（`library/models.py:1124-1143`）：
- 使用 `MoveOperation.MOVE` 模式
- 调用 `util.move()` 移动文件
- 移动后调用 `util.prune_dirs()` 清理空目录
- 同时触发专辑封面的移动

**撤销路径**（部分可逆）：

1. **数据库回退**：无内置回滚机制，需手动操作
   - 操作前建议备份数据库：`cp ~/.config/beets/library.db ~/.config/beets/library.bak`
   - 出问题可直接恢复备份文件

2. **文件回移**：
   - 移动操作会保留完整文件，只是换了位置
   - 可手动将文件移回，然后重新导入：
     ```bash
     beet import /path/to/trash/
     ```

3. **路径格式注意**：
   - 移动后的路径会根据 `path_formats` 重新生成
   - 如果目标目录结构与原库不同，回移时路径可能变化

> **重要**：移动操作会触发 `prune_dirs`（`models.py:1138-1143`），空目录会被自动删除，但文件本身不会丢失，只是目录结构变了。

**风险等级**：中，文件仍在，只是位置变了

---

#### 等级 4：仅移除库引用 (`--remove`)

**命令**：`beet dup --remove`

**效果**：
- 从数据库中删除条目记录
- 磁盘上的文件保留不动
- 相当于"取消收录"，文件仍在原路径

**代码实现**（`duplicates.py:237-238`）：

```python
elif remove:
    item.remove(delete=False)
```

**底层删除逻辑**（`library/models.py:1060-1088`）：

```python
def remove(self, delete=False, with_album=True):
    super().remove()  # 从数据库删除
    
    # 如果是专辑最后一首歌，专辑也会被删除
    if with_album:
        album = self.get_album()
        if album and not album.items():
            album.remove(delete, False)
    
    # delete=False 时不删文件
    if delete:
        util.remove(self.path)
        util.prune_dirs(...)
    
    plugins.send("item_removed", item=self)  # 发送插件信号
```

**数据库层删除**（`dbcore/db.py:704-710`）：
- 从主表（`items`）删除记录
- 从弹性属性表（`item_attributes`）删除关联数据
- 在事务中执行，保证原子性

**撤销路径**（部分可逆）：

1. **重新导入**：文件还在，重新扫描即可
   ```bash
   beet import /path/to/music/
   ```

2. **注意事项**：
   - 重新导入后，条目 ID 会变（自增主键）
   - flexattr（弹性属性）会丢失（因为是关联在条目 ID 上的）
   - 播放次数等统计数据可能需要重新同步

**风险等级**：中低，文件保留，只是库中记录没了

---

#### 等级 5：彻底删除 (`--delete`)

**命令**：`beet dup --delete`

**效果**：
- 从数据库中删除条目
- 从磁盘上删除文件
- 清理空目录
- **不可逆操作**

**代码实现**（`duplicates.py:235-236`）：

```python
if delete:
    item.remove(delete=True)
```

**底层删除逻辑**（`library/models.py:1079-1086`）：

```python
if delete:
    util.remove(self.path)                    # 删除文件
    util.prune_dirs(                          # 清理空目录
        os.path.dirname(self.path),
        self._db.directory,
        clutter=beets.config["clutter"].as_str_seq(),
    )
```

**专辑删除的连锁反应**（`library/models.py:360-384`）：
- 删除专辑时会连带删除所有曲目
- 同时删除专辑封面文件
- 所有操作都是 `delete=True` 模式

**撤销路径**：**无内置撤销机制**

1. **仅有的恢复方式**：
   - 从回收站/废纸篓恢复（如果系统支持）
   - 从备份恢复
   - 使用文件恢复工具（成功率随时间降低）

2. **操作前必须做的防护**：
   - 数据库备份：`cp library.db library.db.bak`
   - 先用 `--tag` 或 `--move` 试运行一轮
   - 用 `--full` 模式确认所有重复项

**风险等级**：极高，文件永久删除

---

### 3.3 动作对比一览表

| 动作 | 参数 | 数据库修改 | 磁盘文件 | 可逆性 | 风险等级 | 撤销方式 |
|------|------|-----------|---------|--------|---------|---------|
| 纯报告 | - | 无 | 无 | - | 极低 | - |
| 打标签 | `--tag` | 修改属性 | 不变 | 完全可逆 | 极低 | `modify` 清除标签 |
| 复制 | `--copy` | 不变 | 新增副本 | 完全可逆 | 低 | 删除副本 |
| 移动 | `--move` | 更新路径 | 位置变化 | 部分可逆 | 中 | 手动移回 + 重新导入 |
| 仅移除库 | `--remove` | 删除记录 | 保留 | 部分可逆 | 中低 | 重新导入 |
| 彻底删除 | `--delete` | 删除记录 | 删除文件 | 不可逆 | 极高 | 无（靠备份） |

---

## 四、合并操作 (`--merge`)

### 4.1 合并机制

合并是一种特殊的"清理"——不是删除，而是将重复条目的信息融合。

**单曲合并**（`_merge_items()`, `duplicates.py:347-369`）：
- 遍历所有字段
- 保留项（排序第一）的字段为空时，从后续条目中取值填充
- 每个字段只从第一个有值的条目中取
- 保留项被修改后 `store()` 存入数据库

**专辑合并**（`_merge_albums()`, `duplicates.py:371-392`）：
- 以首个专辑为基准
- 将后续专辑中缺失的曲目复制过去
- 按 `mb_trackid` 判断曲目是否已存在
- 使用 `MoveOperation.COPY` 模式复制文件

### 4.2 合并 + 删除组合

```bash
beet dup --album --merge --delete
```

执行顺序（`_duplicates()` 方法内）：
1. 分组识别重复
2. 排序确定保留项
3. **合并**元数据/曲目到保留项
4. 输出重复项列表（`--delete` 会删掉这些）

> **注意**：合并操作修改了保留项的数据，这本身也是不可逆的。建议操作前备份数据库。

---

## 五、推荐清理工作流

### 5.1 标准复核流程（7步法）

| 步骤 | 命令 | 目的 | 风险 |
|------|------|------|------|
| 1 | `beet dup -c` | 查看重复数量概览 | 无 |
| 2 | `beet dup --full -f '$path'` | 列出所有重复文件路径 | 无 |
| 3 | `beet dup --tag dup_status=pending` | 给重复项打标记 | 极低 |
| 4 | `beet list dup_status:pending` | 人工复核标记条目 | 无 |
| 5 | `beet dup --move ~/music/.dup_quarantine/` | 移动到隔离区 | 中 |
| 6 | 正常使用一周 | 观察是否有歌曲丢失 | - |
| 7 | 确认无误后删除隔离区 | 最终清理 | 高 |

### 5.2 数据库备份建议

```bash
# 备份数据库
cp ~/.config/beets/library.db ~/.config/beets/library-$(date +%Y%m%d).db

# 如需恢复
cp ~/.config/beets/library-YYYYMMDD.db ~/.config/beets/library.db
```

### 5.3 自定义键的验证方法

如果使用自定义键（`-k`），建议先交叉验证：

```bash
# 先用默认 MusicBrainz ID 查一遍
beet dup -c

# 再用自定义键查一遍
beet dup -k title -k albumartist -k album -c

# 如果数量差异大，说明有漏匹配或误匹配，需要调优
```

---

## 六、常见问题与陷阱

### 6.1 为什么有些重复没检测到？

- **缺少 MusicBrainz ID**：很多盗版/自行抓取的音频没有 MBID
- **解决方案**：使用自定义键 `-k title -k albumartist -k album -k length`

### 6.2 为什么会误报重复？

- **严格模式未开启**：单个字段相同就被分到一组
- **解决方案**：使用 `--strict` 或增加更多匹配键

### 6.3 删除后能恢复吗？

- **数据库层面**：有备份就能恢复
- **文件层面**：删除操作不可逆，务必先 `--move` 观察
- **代码确认**：`util.remove()` 是 `os.remove()` 封装，不经回收站

### 6.4 移动后的文件能回到原来的路径吗？

- 手动移回文件后，使用 `beet update` 或 `beet import` 重新关联
- 但数据库中的条目 ID 会变化（如果是 remove 后重新 import）

---

## 七、代码速查索引

| 功能 | 文件位置 | 方法/行号 |
|------|---------|----------|
| 分组算法 | `beetsplug/duplicates.py` | `_group_by()` L280-308 |
| 排序逻辑 | `beetsplug/duplicates.py` | `_order()` L310-345 |
| 主循环 | `beetsplug/duplicates.py` | `_duplicates()` L405-413 |
| 动作执行 | `beetsplug/duplicates.py` | `_process_item()` L217-245 |
| 单曲删除 | `beets/library/models.py` | `Item.remove()` L1060-1088 |
| 单曲移动 | `beets/library/models.py` | `Item.move()` L1090-1143 |
| 专辑删除 | `beets/library/models.py` | `Album.remove()` L360-384 |
| 数据库删除 | `beets/dbcore/db.py` | `LibModel.remove()` L704-710 |
| 校验和计算 | `beetsplug/duplicates.py` | `_checksum()` L247-278 |
| 单曲合并 | `beetsplug/duplicates.py` | `_merge_items()` L347-369 |
| 专辑合并 | `beetsplug/duplicates.py` | `_merge_albums()` L371-392 |
