#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
河洛存档查看器 (Heluo Save Viewer)
用于查看河洛英雄传/河洛群俠傳的 .save 存档文件内容。
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import msgpack
import lz4.block
import os
import sys

HEADER = b'HELUO_1_1'


def parse_save_file(filepath):
    """解析河洛 .save 存档文件，返回 (save_info, game_data)。"""
    with open(filepath, 'rb') as f:
        data = f.read()

    if not data.startswith(HEADER):
        raise ValueError("不是有效的河洛存档文件（文件头不是 HELUO_1_1）")

    unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
    unpacker.feed(data[len(HEADER):])

    results = []
    for obj in unpacker:
        if not isinstance(obj, msgpack.ExtType) or obj.code != 99:
            raise ValueError(f"数据格式错误：期望 ExtType(99)")

        ext_data = obj.data
        if len(ext_data) < 6:
            raise ValueError("ExtType 数据过短")

        # 前 5 字节：msgpack int32 编码的解压后大小
        decomp_size = msgpack.unpackb(ext_data[:5])
        if not isinstance(decomp_size, int) or decomp_size <= 0:
            raise ValueError(f"无效的解压大小: {decomp_size}")

        # 剩余字节：LZ4 压缩的 MessagePack 数据
        compressed = ext_data[5:]
        decompressed = lz4.block.decompress(compressed, uncompressed_size=decomp_size)
        decoded = msgpack.unpackb(decompressed, raw=False, strict_map_key=False)
        results.append(decoded)

        if len(results) == 2:
            break

    if len(results) < 2:
        raise ValueError(f"存档格式异常：只找到 {len(results)} 个数据块（需要 2 个）")

    return results[0], results[1]


class SaveViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("河洛存档查看器")
        self.root.geometry("960x720")
        self.root.minsize(640, 480)

        self.save_info = None
        self.game_data = None
        self._node_data = {}   # node_id -> python object (lazy loading)
        self._loaded = set()   # already-expanded node ids
        self._next_id = 0

        self._build_ui()

    # ── UI 构建 ──

    def _new_id(self):
        self._next_id += 1
        return f"N{self._next_id}"

    def _build_ui(self):
        # 顶部：文件选择
        top = ttk.Frame(self.root, padding=5)
        top.pack(fill=tk.X)

        ttk.Button(top, text="选择存档文件", command=self._open_file).pack(side=tk.LEFT)
        self.file_label = ttk.Label(top, text="未选择文件", foreground="gray")
        self.file_label.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

        # 摘要面板
        self.summary_frame = ttk.LabelFrame(self.root, text="存档摘要", padding=8)
        self.summary_frame.pack(fill=tk.X, padx=5, pady=(5, 0))
        ttk.Label(self.summary_frame, text="请先选择一个 .save 存档文件",
                  foreground="gray").pack(anchor=tk.W)

        # 角色属性面板
        self.stats_frame = ttk.LabelFrame(self.root, text="角色属性", padding=8)
        self.stats_frame.pack(fill=tk.X, padx=5, pady=(5, 0))
        self.stats_placeholder = ttk.Label(self.stats_frame, text="加载存档后显示",
                                           foreground="gray")
        self.stats_placeholder.pack(anchor=tk.W)

        # 搜索栏
        sf = ttk.Frame(self.root, padding=(5, 5, 5, 0))
        sf.pack(fill=tk.X)
        ttk.Label(sf, text="搜索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        entry = ttk.Entry(sf, textvariable=self.search_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        entry.bind("<Return>", lambda _: self._do_search())
        ttk.Button(sf, text="搜索", command=self._do_search).pack(side=tk.LEFT)
        ttk.Button(sf, text="清除", command=self._clear_search).pack(side=tk.LEFT, padx=(3, 0))

        # 树状视图
        tc = ttk.Frame(self.root)
        tc.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tree = ttk.Treeview(tc, columns=("value",), show="tree headings")
        self.tree.heading("#0", text="字段", anchor=tk.W)
        self.tree.heading("value", text="值", anchor=tk.W)
        self.tree.column("#0", width=360, minwidth=150)
        self.tree.column("value", width=540, minwidth=150)

        ysb = ttk.Scrollbar(tc, orient=tk.VERTICAL, command=self.tree.yview)
        xsb = ttk.Scrollbar(tc, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tc.grid_rowconfigure(0, weight=1)
        tc.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewOpen>>", self._on_expand)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var,
                  relief=tk.SUNKEN, padding=2).pack(fill=tk.X, side=tk.BOTTOM)

    # ── 文件加载 ──

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="选择河洛存档文件",
            filetypes=[("存档文件", "*.save"), ("所有文件", "*.*")]
        )
        if path:
            self._load_file(path)

    def _load_file(self, path):
        self.file_label.config(text=os.path.basename(path), foreground="black")
        self.status_var.set("正在解析存档...")
        self.root.update_idletasks()

        try:
            self.save_info, self.game_data = parse_save_file(path)
        except Exception as e:
            messagebox.showerror("加载失败", str(e))
            self.status_var.set("加载失败")
            return

        self._show_summary()
        self._show_char_stats()
        self._populate_tree()
        self.status_var.set(f"已加载: {os.path.basename(path)}")

    # ── 摘要面板 ──

    def _show_summary(self):
        for w in self.summary_frame.winfo_children():
            w.destroy()

        info = self.save_info
        teammates = info.get("teammateNames", [])
        if isinstance(teammates, list):
            teammates = ", ".join(str(t) for t in teammates)

        fields = [
            ("角色名", info.get("playerName", "—")),
            ("等级", info.get("playerLevel", "—")),
            ("游戏天数", info.get("playedDays", "—")),
            ("难度", info.get("difficulty", "—")),
            ("模组", info.get("modName", "—")),
            ("模组ID", info.get("modId", "—")),
            ("队友", teammates),
            ("追踪任务", info.get("trackedQuestId", "—")),
        ]

        for i, (label, value) in enumerate(fields):
            row = i // 4
            col = (i % 4) * 2
            ttk.Label(self.summary_frame, text=f"{label}:",
                      font=("", 9, "bold")).grid(
                row=row, column=col, sticky=tk.W, padx=(10, 2), pady=2)
            ttk.Label(self.summary_frame, text=str(value)).grid(
                row=row, column=col + 1, sticky=tk.W, padx=(0, 20), pady=2)

    # ── 角色属性面板 ──

    # 属性英文 → 中文名映射
    STAT_NAMES = {
        "HP": "气血", "MaxHP": "最大气血", "MP": "内力", "MaxMP": "最大内力",
        "Physique": "体质", "Intelligence": "悟性", "Speed": "速度",
        "Moral": "道德", "Reputation": "声望", "Money": "银两",
        "Fight": "搏击", "Sword": "剑法", "Blade": "刀法",
        "Spear": "枪法", "Arrow": "弓法", "Short": "暗器",
        "Doctor": "医术", "Poison": "毒术", "Neigong": "内功", "Qinggong": "轻功",
        "HurbKnowledge": "草药", "MineralKnowledge": "矿石",
        "StealKnowledge": "偷窃", "BusinessKnowledge": "经商",
    }

    def _get_stat(self, player_data, key):
        """从 Player.Data 中提取属性值（格式为 {'Base': value}）。"""
        val = player_data.get(key)
        if isinstance(val, dict):
            return val.get("Base", "—")
        return val if val is not None else "—"

    def _show_char_stats(self):
        for w in self.stats_frame.winfo_children():
            w.destroy()

        # 找到主角数据
        chars = self.game_data.get("Character", {})
        player = chars.get("Player")
        if not player or not isinstance(player, dict):
            ttk.Label(self.stats_frame, text="未找到主角数据",
                      foreground="gray").pack(anchor=tk.W)
            return

        pd = player.get("Data", {})

        # ── 第一行：生命/内力 ──
        row = 0
        hp = self._get_stat(pd, "HP")
        max_hp = self._get_stat(pd, "MaxHP")
        mp = self._get_stat(pd, "MP")
        max_mp = self._get_stat(pd, "MaxMP")
        vitals = [
            ("气血", f"{hp} / {max_hp}"),
            ("内力", f"{mp} / {max_mp}"),
            ("等级", player.get("Level", "—")),
            ("经验", f'{player.get("Exp", "—")} / {player.get("MaxExp", "—")}'),
        ]
        for i, (label, value) in enumerate(vitals):
            col = i * 2
            ttk.Label(self.stats_frame, text=f"{label}:",
                      font=("", 9, "bold")).grid(
                row=row, column=col, sticky=tk.W, padx=(10, 2), pady=2)
            ttk.Label(self.stats_frame, text=str(value)).grid(
                row=row, column=col + 1, sticky=tk.W, padx=(0, 20), pady=2)

        # ── 第二行：基础资质 ──
        row = 1
        base_stats = ["Physique", "Intelligence", "Speed", "Money"]
        for i, key in enumerate(base_stats):
            col = i * 2
            cn = self.STAT_NAMES.get(key, key)
            val = self._get_stat(pd, key)
            if key == "Money":
                val = f"{val:,}" if isinstance(val, (int, float)) else val
            ttk.Label(self.stats_frame, text=f"{cn}:",
                      font=("", 9, "bold")).grid(
                row=row, column=col, sticky=tk.W, padx=(10, 2), pady=2)
            ttk.Label(self.stats_frame, text=str(val)).grid(
                row=row, column=col + 1, sticky=tk.W, padx=(0, 20), pady=2)

        # ── 第三行：武学资质 ──
        row = 2
        combat = ["Fight", "Sword", "Blade", "Spear"]
        for i, key in enumerate(combat):
            col = i * 2
            cn = self.STAT_NAMES.get(key, key)
            val = self._get_stat(pd, key)
            ttk.Label(self.stats_frame, text=f"{cn}:",
                      font=("", 9, "bold")).grid(
                row=row, column=col, sticky=tk.W, padx=(10, 2), pady=2)
            ttk.Label(self.stats_frame, text=str(val)).grid(
                row=row, column=col + 1, sticky=tk.W, padx=(0, 20), pady=2)

        # ── 第四行：武学资质续 + 其他 ──
        row = 3
        combat2 = ["Arrow", "Short", "Doctor", "Poison"]
        for i, key in enumerate(combat2):
            col = i * 2
            cn = self.STAT_NAMES.get(key, key)
            val = self._get_stat(pd, key)
            ttk.Label(self.stats_frame, text=f"{cn}:",
                      font=("", 9, "bold")).grid(
                row=row, column=col, sticky=tk.W, padx=(10, 2), pady=2)
            ttk.Label(self.stats_frame, text=str(val)).grid(
                row=row, column=col + 1, sticky=tk.W, padx=(0, 20), pady=2)

        # ── 第五行：内功轻功 + 声望道德 ──
        row = 4
        misc = ["Neigong", "Qinggong", "Moral", "Reputation"]
        for i, key in enumerate(misc):
            col = i * 2
            cn = self.STAT_NAMES.get(key, key)
            val = self._get_stat(pd, key)
            ttk.Label(self.stats_frame, text=f"{cn}:",
                      font=("", 9, "bold")).grid(
                row=row, column=col, sticky=tk.W, padx=(10, 2), pady=2)
            ttk.Label(self.stats_frame, text=str(val)).grid(
                row=row, column=col + 1, sticky=tk.W, padx=(0, 20), pady=2)

    # ── 树状视图 ──

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._node_data.clear()
        self._loaded.clear()
        self._next_id = 0

        self._insert_node("", "存档信息 (save_info)", self.save_info)
        self._insert_node("", "游戏数据 (game_data)", self.game_data)

    def _format_value(self, val):
        if isinstance(val, bytes):
            if val[:4] == b'\x89PNG':
                return f"<PNG 图片: {len(val):,} bytes>"
            return f"<二进制数据: {len(val):,} bytes>"
        if isinstance(val, str) and len(val) > 300:
            return val[:300] + f"... (共 {len(val):,} 字符)"
        if val is None:
            return "null"
        if isinstance(val, bool):
            return "True" if val else "False"
        return str(val)

    def _insert_node(self, parent, text, value):
        nid = self._new_id()
        if isinstance(value, dict):
            self.tree.insert(parent, tk.END, iid=nid, text=text,
                             values=(f"{{...}} ({len(value)} 个字段)",))
            self._node_data[nid] = value
            # placeholder child so the expand arrow shows
            self.tree.insert(nid, tk.END, iid=self._new_id())
        elif isinstance(value, list):
            self.tree.insert(parent, tk.END, iid=nid, text=text,
                             values=(f"[...] ({len(value)} 个元素)",))
            self._node_data[nid] = value
            self.tree.insert(nid, tk.END, iid=self._new_id())
        else:
            self.tree.insert(parent, tk.END, iid=nid, text=text,
                             values=(self._format_value(value),))
        return nid

    def _on_expand(self, _event):
        nid = self.tree.focus()
        if nid in self._loaded:
            return
        self._loaded.add(nid)

        data = self._node_data.get(nid)
        if data is None:
            return

        # 删除 placeholder
        for child in self.tree.get_children(nid):
            self.tree.delete(child)

        if isinstance(data, dict):
            for key in data:
                self._insert_node(nid, str(key), data[key])
        elif isinstance(data, list):
            for i, item in enumerate(data):
                self._insert_node(nid, f"[{i}]", item)

    # ── 搜索 ──

    def _do_search(self):
        query = self.search_var.get().strip()
        if not query:
            return

        if not self.save_info and not self.game_data:
            messagebox.showinfo("提示", "请先加载存档文件")
            return

        self.status_var.set("正在搜索...")
        self.root.update_idletasks()

        results = []
        if self.save_info:
            self._search_in(self.save_info, "save_info", query.lower(), results)
        if self.game_data:
            self._search_in(self.game_data, "game_data", query.lower(), results)

        if not results:
            self.status_var.set(f"未找到匹配: \"{query}\"")
            messagebox.showinfo("搜索结果", f"未找到与 \"{query}\" 匹配的内容")
            return

        self.status_var.set(f"找到 {len(results)} 处匹配")
        self._show_search_results(query, results)

    def _search_in(self, data, path, query, results, depth=0):
        if depth > 20 or len(results) >= 500:
            return

        if isinstance(data, dict):
            for key, val in data.items():
                ks = str(key)
                cp = f"{path}.{ks}"
                # 匹配 key 名
                if query in ks.lower():
                    if isinstance(val, (dict, list)):
                        results.append((cp, f"({type(val).__name__}, {len(val)} items)"))
                    else:
                        results.append((cp, self._format_value(val)))
                # 递归或匹配值
                if isinstance(val, (dict, list)):
                    self._search_in(val, cp, query, results, depth + 1)
                elif isinstance(val, str) and query in val.lower():
                    results.append((cp, val[:200]))
        elif isinstance(data, list):
            for i, item in enumerate(data):
                cp = f"{path}[{i}]"
                if isinstance(item, (dict, list)):
                    self._search_in(item, cp, query, results, depth + 1)
                elif isinstance(item, str) and query in item.lower():
                    results.append((cp, item[:200]))

    def _show_search_results(self, query, results):
        win = tk.Toplevel(self.root)
        win.title(f"搜索结果: \"{query}\" ({len(results)} 条)")
        win.geometry("780x480")
        win.transient(self.root)

        frame = ttk.Frame(win)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        rtree = ttk.Treeview(frame, columns=("path", "value"), show="headings")
        rtree.heading("path", text="路径", anchor=tk.W)
        rtree.heading("value", text="值", anchor=tk.W)
        rtree.column("path", width=380, minwidth=150)
        rtree.column("value", width=380, minwidth=150)

        rsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=rtree.yview)
        rtree.configure(yscrollcommand=rsb.set)
        rtree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rsb.pack(side=tk.RIGHT, fill=tk.Y)

        for path, val in results:
            rtree.insert("", tk.END, values=(path, str(val)))

    def _clear_search(self):
        self.search_var.set("")
        self.status_var.set("就绪")


def main():
    root = tk.Tk()
    app = SaveViewerApp(root)

    # 支持命令行传入文件路径（或拖放到 exe 上）
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        root.after(100, lambda: app._load_file(sys.argv[1]))

    root.mainloop()


if __name__ == "__main__":
    main()
