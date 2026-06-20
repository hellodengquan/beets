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

**底层文件移动的完整实现**（`beets/util/__init__.py:492-550`）：

```python
def move(path: bytes, dest: bytes, replace: bool = False):
    # 先尝试 os.replace() 原子重命名（同分区）
    try:
        os.replace(syspath(path), syspath(dest))
    except OSError:
        # 跨分区时：先复制到临时文件 .beets，再替换目标，最后删源文件
        tmp = tempfile.NamedTemporaryFile(
            suffix=".beets",
            prefix=f".{basename}.",
            dir=syspath(dirname),
            delete=False,
        )
        with open(syspath(path), "rb") as f:
            shutil.copyfileobj(f, tmp)
        shutil.copystat(syspath(path), tmp.name)   # 复制权限与时间戳
        os.replace(tmp_filename, syspath(dest))    # 原子替换目标
        os.remove(syspath(path))                    # 最后才删源文件
```

> **关键观察**：
> - 同分区下是纯 `os.replace()` 原子操作，不产生副本
> - 跨分区时**先复制再删源**，源文件在最后一步才被 `os.remove()` 硬删
> - 全程没有任何"复制到回收站/trash bin"的中间层——要么留在原位，要么彻底移走
> - 临时文件命名为 `.原始文件名.xxxx.beets`，放在目标目录下，若中途中断可手动恢复

**撤销路径（代码级分析）**：

1. **数据库层撤销**：移动后立即调用 `item.store()`（`duplicates.py:234`）将新路径写回 DB，整个过程在事务外执行，**无内置回滚机制**。
   - 唯一保护手段：操作前备份 SQLite 文件
     ```bash
     cp ~/.config/beets/library.db ~/.config/beets/library-predup-$(date +%Y%m%d%H%M%S).db
     ```
   - 若误操作，关闭 beets 相关进程后直接用备份覆盖即可还原所有路径引用

2. **文件层撤销**：
   - **同分区场景**：源路径 → 目标路径是纯 rename，inode 不变。只需在 shell 执行反向 `mv` 即可物理还原：
     ```bash
     # 将隔离区里的文件移回原库目录，然后重新扫描
     mv ~/music/.dup_quarantine/* ~/music/library/
     beet import ~/music/library/
     ```
   - **跨分区场景**：复制阶段生成的 `.xxx.beets` 临时文件仅在 `OSError` 分支存在，正常移动完成后已被 `os.remove(tmp_filename)` 清理（`util/__init__.py:549-550`），**移动成功后无残留临时副本可供恢复**
   - 如果移动后文件在新目录里没被修改过，文件本身完整无损，反向移动即可

3. **路径重生成的副作用**：
   - 目标路径由 `item.destination(basedir=move)` 生成（`models.py:1118`），会套用当前全局 `path_formats` 配置
   - 如果 `--move` 指定的 basedir 与原库目录不同，路径模板会重新计算，文件名可能变化（如艺术家名修正后）
   - 撤销时需要先 `beet list dup_status:pending -f '$path'` 导出新路径，再逐条 mv 回去

4. **空目录清理的影响**：移动成功后 `prune_dirs()` 会向上递归清理空目录（`models.py:1138-1143`），直到遇到 `library.directory` 根目录才停止。这意味着：
   - 撤销时若原目录已被清理，需要 `mkdir -p` 重建目录树
   - 专辑封面文件会随 `Album.move_art()` 同步移动（`models.py:1130-1135`），撤销时封面也需要一起移回

**风险等级**：中，文件完整保留但路径 + 数据库记录均已改变，无一键回滚

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

**硬删不经回收站的代码证据**（`beets/util/__init__.py:457-469`）：

```python
def remove(path: PathLike, soft: bool = True):
    """Remove the file. If `soft`, then no error will be raised if the
    file does not exist.
    """
    str_path = syspath(path)
    if not str_path or (soft and not os.path.exists(str_path)):
        return
    try:
        os.remove(str_path)          # ← 直接调用 POSIX unlink
    except OSError as exc:
        raise FilesystemError(
            exc, "delete", (str_path,), traceback.format_exc()
        )
```

> **重要结论**：
> - 底层使用的是 `os.remove()`，即系统调用 `unlink(2)`
> - **没有任何 trash bin / Recycle Bin / freedesktop Trash 中间层**
> - 不存在"删除后可以在系统回收站找回"的假设——一旦执行，inode 被释放
> - macOS 上不会走 Finder 的 "Put Back"，Linux 上不会走 `~/.local/share/Trash`，Windows 上不会走 `$Recycle.Bin`
> - 唯一条件分支是 `soft=True`（默认）时，文件不存在不报异常，不影响硬删本身

**专辑删除的连锁硬删**（`library/models.py:360-384`）：

```python
def remove(self, delete=False, with_items=True):
    super().remove()                     # 删数据库专辑记录
    if delete:
        artpath = self.artpath
        if artpath:
            util.remove(artpath)         # ← 封面文件硬删
    if with_items:
        for item in self.items():
            item.remove(delete, False)   # ← 所有曲目级联硬删
```

每个曲目又会触发自身的 `util.remove(item.path)` + `prune_dirs()` 清理空目录。**整个专辑删除流程产生的所有文件删除操作，全部绕过任何回收站机制。**

**撤销路径：无内置撤销机制**

1. **数据库层恢复**：
   - 从备份恢复 SQLite 文件即可还原条目 + 元数据 + flexattr
   - 但文件 inode 已释放，数据库恢复后路径指向的文件可能已不存在

2. **文件层恢复**（唯一实际手段）：
   - **不存在系统回收站兜底**，别指望 macOS Trash / Linux Trash / Windows Recycle Bin
   - 如果有文件级备份（Time Machine、rsync 快照、git-annex 等），从备份还原
   - 如果没有备份，只能使用底层数据恢复工具（如 `testdisk`、` photorec`），且成功率取决于磁盘未被覆写的程度——**越早操作越好**

3. **操作前必须做的防护**：
   - 数据库备份：`cp library.db library-pre-del-$(date +%Y%m%d%H%M%S).db`
   - 先用 `--tag` 标记，人工 `beet list dup:1` 复核
   - 再用 `--move ~/music/.dup_quarantine/` 隔离，正常使用一周确认无丢失
   - 最后才 `--delete`（或直接手动 `rm` 隔离区，可更小心控制）

4. **关于 import 命令内的删除**：`beet import --delete` 同样走 `util.remove()`（`importer/tasks.py:356`），也不经回收站。同理 `beet convert --keep-new --delete-originals` 也是 `util.remove(source_path)`（`convert.py:692-698`），**整个 beets 生态中不存在任何"安全删除"抽象层**。

**为何不集成 send2trash 等跨平台垃圾箱库**：

1. **依赖缺失**：`pyproject.toml` 中完全没有 `send2trash`、`trash-cli`、`Send2Trash` 或任何垃圾箱相关依赖。整个代码库 `grep -ri "send2trash\|trash\|recycle"` 无任何结果（除本文档外）。

2. **设计哲学考量**：
   - beets 定位为"严肃的音乐库管理工具"，CLI 工具默认假定用户理解 `--delete` 的含义
   - 跨平台垃圾箱行为不一致：macOS Finder 的"Put Back"、Linux freedesktop `~/.local/share/Trash`、Windows `$Recycle.Bin` 在 headless 服务器、Docker 容器、网络文件系统上经常不可用
   - 已有更可控的替代方案：`--move` 到隔离区，由用户最终决定是否 `rm`，比依赖系统回收站更可靠

3. **用户可自行扩展**：如果需要垃圾箱功能，可用 `--move` 指向系统垃圾箱路径模拟：
   ```bash
   # macOS
   beet dup --move ~/.Trash/
   # Linux (freedesktop)
   beet dup --move ~/.local/share/Trash/files/
   ```

**风险等级**：极高，文件永久释放，无任何兜底

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

### 3.4 批量清理的事务回滚与部分失败处理

**主循环无事务包裹**（`duplicates.py:190-212`）：

```python
for obj_id, obj_count, objs in self._duplicates(...):
    if obj_id:
        for o in objs:
            self._process_item(o, ...)  # 每个 item 独立处理
```

> **关键事实**：
> - 外层 `for o in objs:` 循环**没有任何 try-except**，也**没有包裹在数据库事务中**
> - `_process_item()` 内部也没有 try-except 包装（除了 `--tag` 解析错误会 `raise UserError`）
> - 一个 item 处理失败（如文件被占用、权限不足）会**立即终止整个批处理**，已处理的 item **不会回滚**

**单个 item 的处理原子性**：

每个 `item.remove(delete=True)` 的内部执行顺序（`library/models.py:1060-1086`）：

```python
def remove(self, delete=False, with_album=True):
    super().remove()  # 1. 数据库删除（事务内）
    # ...
    if delete:
        util.remove(self.path)     # 2. 文件硬删（事务外！）
        util.prune_dirs(...)        # 3. 目录清理（事务外！）
```

**数据库层事务**（`dbcore/db.py:704-710`）仅包裹 SQL 操作：
```python
def remove(self):
    with self.db.transaction() as tx:
        tx.mutate(f"DELETE FROM {self._table} WHERE id=?", (self.id,))
        tx.mutate(f"DELETE FROM {self._flex_table} WHERE entity_id=?", (self.id,))
```

**文件系统操作在事务外**。`Transaction.__exit__` 方法（`dbcore/db.py:988-1015`）仅处理数据库回滚：
- 异常发生时，SQLite 事务会回滚（`return None` 不 suppress 异常）
- 但 `util.remove()` 已经执行过的文件删除**不会回滚**

**部分失败的典型场景**：

假设批量清理 100 个重复项，第 50 个文件被另一进程锁定：

| 阶段 | 已处理 49 个 | 第 50 个 | 剩余 50 个 |
|------|-------------|----------|-----------|
| 数据库删除 | ✓ 已提交 | ✗ 回滚 | ✗ 未执行 |
| 文件硬删 | ✓ 已硬删 | ✗ 未执行（`OSError`） | ✗ 未执行 |
| 目录清理 | ✓ 已清理 | ✗ 未执行 | ✗ 未执行 |

**结果**：
- 前 49 个文件永久删除，数据库记录也永久删除
- 第 50 个文件完好，但因 SQL 回滚数据库记录也完好
- 第 51-100 个完全未动
- **无任何自动回滚机制**，出现"半删除"不一致状态

**兜底手段**：

1. **数据库备份**：批量操作前必须备份 SQLite 文件（参见 5.2 节）
2. **分小批量执行**：用查询分片控制每次处理数量
   ```bash
   # 每次只处理 10 个，降低失败影响面
   beet dup -k title -k albumartist --delete added-
   ```
3. **先 `--move` 后 `--delete`**：移动操作是可逆的（文件还在），确认无误再删
4. **`--pretend` 预演**：convert 等命令支持 `--pretend`，但 duplicates 插件**不支持**，只能靠 `--tag` 打标后人工复核

**代码验证**：duplicates 插件没有任何 `--pretend`/`--dry-run` 逻辑，也没有 `try: ... except: ... rollback` 结构。整个批处理是"尽力而为"的线性执行，失败即中断，无回滚。

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

## 七、与 import / convert 等主线命令的交互关系

### 7.1 duplicates 插件不会监听 import 流程

`duplicates` 插件本身只注册了一个子命令 `duplicates`/`dup`，**没有注册任何 import 相关的事件监听器或 import_stage**。

代码验证（`beetsplug/duplicates.py:34-60`）：
```python
class DuplicatesPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        self.config.add({...})
        self._command = Subcommand("duplicates", ...)
        # 没有 self.import_stages = [...]
        # 没有 self.register_listener("import_task_xxx", ...)
        # 没有 self.early_import_stages = [...]
```

> **结论**：执行 `beet import` 时，`duplicates` 插件完全不介入。它是一个独立的、手动触发的后处理命令。

### 7.2 import 命令有自己独立的重复检测分组规则

import 流程内部自带重复检测机制，其分组键与 `duplicates` 插件**完全独立、互不覆盖**。

**import 侧的分组键**（`config_default.yaml:50-52`）：

```yaml
import:
  duplicate_keys:
    album: albumartist album      # import 专辑匹配键
    item: artist title            # import 单曲匹配键
  duplicate_action: ask           # 遇到重复时的处理策略
```

**import 侧的匹配实现**（`importer/tasks.py:391-422`）：

```python
def find_duplicates(self, lib):
    info = self.chosen_info()
    tmp_album = library.Album(lib, **info)
    keys = config["import"]["duplicate_keys"]["album"].as_str_seq()
    dup_query = tmp_album.duplicates_query(keys)   # AndQuery 精确匹配
    # 遍历库中相同 artist+album 的专辑，排除同路径完全重导入
```

**import 侧的删除行为**（`importer/tasks.py:272-292`）：

```python
def remove_duplicates(self, lib):
    duplicate_albums = self.find_duplicates(lib)
    for album in duplicate_albums:
        for item in album.items():
            item.remove(with_album=False)
            if lib.directory in util.ancestry(item.path):
                util.remove(item.path)    # ← 同样走硬删
                util.prune_dirs(...)
```

### 7.3 两套分组规则对比

| 维度 | `beet dup`（duplicates 插件） | `beet import`（内置重复检测） |
|------|-------------------------------|------------------------------|
| **默认分组键** | 单曲：`mb_trackid, mb_albumid`<br>专辑：`mb_albumid` | 单曲：`artist, title`<br>专辑：`albumartist, album` |
| **匹配精度** | MusicBrainz GUID，全局唯一 | 文本字段，可能误匹配 |
| **可配置性** | `-k` 任意字段组合，支持多键 | `import.duplicate_keys` 配置项 |
| **严格模式** | `--strict` 要求所有键非空 | 无严格模式，artist 为 None 时直接跳过 |
| **触发时机** | 手动后处理，对已入库数据 | 导入过程中自动触发 |
| **结果处理** | tag / copy / move / remove / delete / merge | 交互 ask / 自动 remove + delete |
| **排序/保留项** | `tiebreak` 或完整性优先 | 保留库中原条目，删除新导入的重复 |

> **核心区别**：import 侧用的是**弱文本键**（artist+title），只在导入时用来拦截重复入库；duplicates 插件默认用的是**强 GUID 键**（MBID），用来事后精准清理已入库的重复。两套规则独立运行，互不覆盖——**import 的 duplicate_keys 配置不会影响 `beet dup` 的分组，反之亦然。**

### 7.4 convert 命令对重复检测的影响

`convert` 插件（`beetsplug/convert.py`）有两种工作模式，对 duplicates 分组规则的影响不同：

#### 模式 A：手动 `beet convert`（不影响分组键）

- 转换后的文件路径被更新（`convert.py:502-504`）：
  ```python
  item.path = converted
  item.read()       # 重新读取音频属性（比特率、时长等可能变化）
  item.store()
  ```
- 如果 duplicates 分组键中包含了 `bitrate`、`length`、`path` 等会变的字段，**转换前后相同内容的文件可能不再被分到一组**
- 如果分组键是 `mb_trackid` 等 GUID，则不受转换影响

#### 模式 B：import 时自动转换（可能引入新重复）

`convert` 注册了 `early_import_stages`（`convert.py:126`）：
```python
self.early_import_stages = [self.auto_convert, self.auto_convert_keep]
```

`auto_convert` 的副作用（`convert.py:662-698`）：
```python
def convert_on_import(self, _, item):
    if self.should_transcode(item):
        # 创建临时转码文件
        fd, dest = tempfile.mkstemp(b"." + ext, dir=tmpdir)
        self.encode(command, item.path, dest)
        
        # 用转码后的临时文件替换库中条目路径
        source_path = item.path
        item.path = dest
        item.write()
        item.read()
        item.store()
        
        if self.config["delete_originals"]:
            util.remove(source_path, False)   # ← 原文件被硬删
```

**对 duplicates 分组的影响链**：
1. 转码后 `bitrate`、`length`、`path` 等字段变化
2. 若分组键包含这些字段，**原文件的同组条目会"脱钩"，不再被识别为重复**
3. 若开启 `delete_originals`，原文件被删，库中只剩转码条目，后续 `beet dup` 看不到重复
4. 若未开启 `delete_originals`，原文件还在磁盘但不在库中，下次 `beet import` 可能再次引入（变成"转码版 + 原版"并存，需要用自定义键 `-k title -k albumartist` 才能识别）

### 7.5 典型组合使用场景的分组规则

| 场景 | 实际生效的分组规则 | 注意事项 |
|------|-------------------|---------|
| 先 `beet import`，后 `beet dup` | import 用 `artist+title`（防重）<br>dup 用 `mb_trackid+mb_albumid`（清理） | 两套独立，互不干扰 |
| `beet import --delete` 后跑 dup | 旧重复已被 import 侧硬删，dup 可能查不到 | import 侧也走 `util.remove()` 硬删 |
| `beet convert --keep-new` 后跑 dup | 新文件替换了旧路径，若分组键含 `bitrate` 可能漏检 | 建议用 MBID 分组，不受转码影响 |
| import 时开启 convert auto_convert | 转码在入库早期执行，后续入库的是转码后路径 | 原版文件若未被删，可能成为"未入库的幽灵文件" |
| `beet dup -k bitrate -k length` + convert | 相同内容转码前后比特率不同，不会被归为重复 | 内容级重复检测应使用 `--checksum` |

> **最佳实践**：清理重复与格式转换分两步进行——先用 `beet dup` 基于 MBID 或 checksum 清理干净，再执行 `convert`。顺序反过来可能导致分组键值变化，漏检可清理的重复。

---

### 7.6 与 fetchart、embedart 等下载型插件的钩子交互

#### 7.6.1 事件监听器对比

首先明确：**duplicates 插件不注册任何事件监听器**，它是一个纯命令驱动的后处理工具。

**各插件监听的事件**：

| 插件 | `import_stages` | `register_listener` 监听的事件 | 触发时机 |
|------|----------------|------------------------------|---------|
| **duplicates** | 无 | 无 | 仅手动执行 `beet dup` 时 |
| **fetchart** | `[self.fetch_art]` | `import_task_files` → `self.assign_art` | 导入时抓取封面 |
| **embedart** | 无 | `import_task_files` → `self.import_task_files` | 导入时嵌入封面 |
| **lyrics** | `[self.imported]` | 无 | 导入后抓取歌词 |
| **lastgenre** | `[self.imported]` | 无 | 导入后补全流派 |
| **scrub** | 无 | `import_task_files` → `self.import_task_files` | 导入后清理元数据 |

代码验证（`duplicates.py:34-61`）：
```python
class DuplicatesPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()
        self.config.add({...})
        self._command = Subcommand("duplicates", ...)
        # 无 self.import_stages = [...]
        # 无 self.register_listener(...)
```

#### 7.6.2 无直接钩子冲突，但存在间接影响

**结论**：duplicates 插件与 fetchart 等下载型插件**不存在直接的钩子冲突**——它们监听的事件完全不重叠，执行时机完全分离。

但存在以下**间接交互**：

1. **fetchart 修改 `artpath` 字段**：
   - `fetchart.fetch_art()` 下载封面后 `item.store()`（`fetchart.py:1403`）
   - `fetchart.assign_art()` 设置 `album.artpath` 后 `album.store()`
   - 如果 duplicates 的 `tiebreak.items` 配置包含 `artpath` 或封面相关字段，**导入前后排序优先级可能变化**

2. **embedart 修改文件元数据**：
   - `embedart.import_task_files()` 写入内嵌封面到文件
   - 文件修改后 `mtime` 变化，若分组键含 `mtime` 会受影响
   - 但默认分组键是 MBID，不受影响

3. **事件广播的接收差异**：
   - duplicates 执行 `item.remove()` 时会发送 `item_removed` 事件（`library/models.py:1077`）
   - `plugins.send("item_removed", item=self)` 会广播给所有监听该事件的插件
   - 但 fetchart/embedart/lyrics 等**都不监听** `item_removed` 事件（代码搜索无匹配）
   - **不会出现"删了条目但封面还在下载"的竞态问题**

4. **`database_change` 事件**：
   - `item.store()` 和 `item.remove()` 都会发送 `database_change` 事件（`library/models.py:86-91`）
   - fetchart 不监听此事件，无副作用

#### 7.6.3 典型组合使用的注意事项

| 场景 | 行为分析 | 风险 |
|------|---------|------|
| 先 `import`（fetchart/embedart 自动运行），后 `beet dup` | 下载型插件在 import 时已完成工作，duplicates 事后清理无冲突 | 低 |
| `beet fetchart` 手动补封面期间跑 `beet dup --delete` | fetchart 正在写入文件时，duplicates 可能尝试删同一个文件 → `OSError` 导致批处理中断 | 中 |
| 自定义 `tiebreak: [artpath]` + fetchart | 有封面的条目排序优先，无封面的可能被误删 | 中 |
| `beet dup --merge --delete` + 刚导入的新条目 | merge 时可能从 fetchart 刚补的封面条目中取值填充 | 低（有益） |

**规避方案**：
1. 顺序执行：先完成 import 让所有下载型插件跑完，再执行 duplicates 清理
2. 避免在 `tiebreak` 中使用可能动态变化的字段（如 `artpath`、`mtime`）
3. 手动 `beet fetchart` 批量补封面时，不要并行跑 `beet dup --delete`

#### 7.6.4 插件事件系统的执行模型

`plugins.send()` 是**同步顺序执行**的（`plugins.py:642-655`）：
```python
def send(event, **arguments):
    log.debug("Sending event: {}", event)
    return [
        r
        for handler in BeetsPlugin.listeners[event]
        if (r := handler(**arguments)) is not None
    ]
```

- 无并行，无竞态
- 监听者列表固定，duplicates 不注册任何监听
- 因此不存在"多个插件同时修改同一个 item"的并发问题

---

## 八、代码速查索引

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
| **硬删核心（无trash）** | `beets/util/__init__.py` | `remove()` L457-469 |
| **文件移动实现** | `beets/util/__init__.py` | `move()` L492-550 |
| **空目录清理** | `beets/util/__init__.py` | `prune_dirs()` L309-343 |
| **import 侧重复键** | `beets/config_default.yaml` | `import.duplicate_keys` L50-52 |
| **import 侧重复检测** | `beets/importer/tasks.py` | `find_duplicates()` L391-422 / L711-730 |
| **import 侧删重复** | `beets/importer/tasks.py` | `remove_duplicates()` L272-292 / L734-746 |
| **convert 导入时转码** | `beetsplug/convert.py` | `auto_convert()` / `convert_on_import()` L662-698 |
| **事务回滚机制** | `beets/dbcore/db.py` | `Transaction.__exit__()` L988-1015 |
| **事件广播机制** | `beets/plugins.py` | `send()` L642-655 |
| **fetchart 注册监听** | `beetsplug/fetchart.py` | `__init__` L1403-1404 |
