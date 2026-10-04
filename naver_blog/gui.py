"""블로그 자동화 프로그램 창. 0_프로그램_창.bat 으로 연다.

- 실행: 지금까지 쓰던 bat 파일들을 버튼으로 (각각 새 검은 창에서 돌아가므로 번호 입력 같은 것도 그대로)
- 키워드 목록: keywords.csv 를 표로 보며 키워드·메모·카테고리를 넣고 고치기 (엑셀·메모장 칸 맞출 필요 없음)
- 글자 색: 소제목·인용구·핵심 한 줄·도입부 색을 색상표에서 골라 config.toml 에 저장
"""

import json
import os
import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, messagebox, ttk

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.toml"

BUTTONS = [
    ("글쓰기", [
        ("글 1개 써서 임시저장", "4_run_save_draft.bat", "keywords.csv 에서 상태가 빈 키워드 하나로 글을 써서 임시저장해요"),
        ("미리보기만 (저장 안 함)", "3_test_write_only.bat", "블로그에 올리지 않고 output 폴더에 미리보기만 만들어요"),
        ("클립 영상 만들기", "8_make_clip.bat", "오늘 쓴 글로 네이버 클립용 세로 영상을 만들어요"),
    ]),
    ("키워드 찾기", [
        ("지금 뜨는 키워드", "11_trending_keywords.bat", "크리에이터 어드바이저 인기 유입 검색어 (이슈)"),
        ("평생 키워드", "12_evergreen_keywords.bat", "1년 내내 꾸준히 검색되는 키워드 (연금형)"),
        ("주제로 키워드 찾기", "6_keyword_finder.bat", "큰 주제를 넣으면 롱테일 키워드를 찾아요"),
        ("시즌 키워드", "7_season_keywords.bat", "다가오는 계절·행사 키워드"),
        ("키워드 자동 채우기", "13_autofill_keywords.bat", "평생 + 지금 뜨는 키워드에서 좋은 것을 알아서 골라 넣어요"),
    ]),
    ("준비·설정", [
        ("네이버 로그인", "2_login.bat", "로그인이 풀렸을 때 다시 로그인해요"),
        ("사진 폴더 만들기", "2-5_make_photo_folders.bat", "키워드별 내 사진 폴더를 만들어요"),
        ("목소리 테스트", "9_voice_test.bat", "클립 목소리를 미리 들어봐요"),
        ("설치 (처음 한 번)", "1_install.bat", "필요한 프로그램을 설치해요"),
    ]),
]

STYLE_KEYS = [
    ("heading_color", "소제목", "#00756a"),
    ("quote_color", "인용구(포스트잇) 글자", "#00756a"),
    ("key_color", "핵심 한 줄 (밑줄 문장)", "#d9480f"),
    ("intro_color", "도입 3줄", "#777777"),
    ("q_color", "Q&A 질문", "#00756a"),
    ("a_color", "Q&A 답변", "#666666"),
]


def run_bat(name: str) -> None:
    """bat 파일을 새 검은 창에서 실행 (창 안에서 번호 입력·엔터가 그대로 된다)"""
    bat = ROOT / name
    if not bat.exists():
        messagebox.showerror("파일 없음", f"{name} 파일이 없어요. 최신 업데이트를 덮어써 주세요.")
        return
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=ROOT)
    else:  # 윈도우가 아닌 곳에서 시험할 때
        subprocess.Popen(["sh", str(bat)], cwd=ROOT)


def open_path(p: Path) -> None:
    if os.name == "nt":
        os.startfile(p)  # noqa
    else:
        subprocess.Popen(["xdg-open", str(p)])


# ── 키워드 목록 ──────────────────────────────────────────────────

SHOP_CATEGORIES = ["주방템", "청소·세탁템", "수납·정리템", "자취 꿀템 모음"]
SHOP_HIDDEN = {"11_trending_keywords.bat", "12_evergreen_keywords.bat", "7_season_keywords.bat", "13_autofill_keywords.bat"}


def is_shop() -> bool:
    try:
        from main import load_config
        return bool(load_config().get("shopping", {}).get("enabled"))
    except Exception:
        return False


def known_categories(rows: list[dict]) -> list[str]:
    cats = []
    try:
        cats = list(json.loads((ROOT / "categories.json").read_text(encoding="utf-8")).keys())
    except Exception:
        pass
    for r in rows:
        if r.get("category") and r["category"] not in cats:
            cats.append(r["category"])
    if is_shop():
        return cats + [c for c in SHOP_CATEGORIES if c not in cats]
    return cats or ["생활정보", "경제·재테크", "IT·AI", "연예·이슈 기록"]


class KeywordTab(ttk.Frame):
    COLS = (("keyword", "키워드", 220), ("memo", "메모 (직접 겪은 일·궁금한 점)", 360),
            ("category", "카테고리", 120), ("status", "상태", 170))

    def __init__(self, master):
        super().__init__(master, padding=10)
        from main import load_config, load_rows, save_rows
        self._load, self._save = load_rows, save_rows
        self.rows: list[dict] = []
        try:
            self.shop = bool(load_config().get("shopping", {}).get("enabled"))
        except Exception:
            self.shop = False
        cols = self.COLS + ((("link", "상품 링크", 200),) if self.shop else ())

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="상태가 빈 줄부터 차례로 글을 써요. 줄을 누르면 아래에서 고칠 수 있어요.").pack(side="left")
        self.only_todo = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="아직 안 쓴 것만 보기", variable=self.only_todo, command=self.refresh).pack(side="right")

        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, pady=6)
        self.tree = ttk.Treeview(box, columns=[c for c, _, _ in cols], show="headings", height=14)
        for c, title, w in cols:
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        form = ttk.LabelFrame(self, text="키워드 넣기 / 고치기", padding=8)
        form.pack(fill="x")
        self.kw, self.memo, self.cat, self.link = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(form, text="키워드").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.kw, width=30).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Label(form, text="카테고리").grid(row=0, column=2, sticky="w")
        self.cat_box = ttk.Combobox(form, textvariable=self.cat, width=18)
        self.cat_box.grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(form, text="메모").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(form, textvariable=self.memo, width=80).grid(row=1, column=1, columnspan=3, sticky="we", padx=4)
        if self.shop:
            ttk.Label(form, text="상품 링크").grid(row=2, column=0, sticky="w", pady=4)
            ttk.Entry(form, textvariable=self.link, width=80).grid(row=2, column=1, columnspan=3, sticky="we", padx=4)
            ttk.Label(form, text="메모에 직접 써 본 경험을 적으면 실사용 리뷰, 비워 두면 구매 가이드로 써요. 링크는 쇼핑커넥트에서 복사해 붙여 넣기",
                      foreground="#777").grid(row=3, column=1, columnspan=3, sticky="w")
        form.columnconfigure(1, weight=1)
        btns = ttk.Frame(form)
        btns.grid(row=4, column=0, columnspan=4, sticky="e", pady=(6, 0))
        ttk.Button(btns, text="새로 추가", command=self.add).pack(side="left", padx=3)
        ttk.Button(btns, text="선택한 줄 고치기", command=self.update_row).pack(side="left", padx=3)
        ttk.Button(btns, text="다시 쓰게 하기 (상태 비우기)", command=self.reset_status).pack(side="left", padx=3)
        ttk.Button(btns, text="선택한 줄 지우기", command=self.delete).pack(side="left", padx=3)
        ttk.Button(btns, text="안 맞는 키워드 정리", command=self.prune).pack(side="left", padx=3)
        ttk.Button(btns, text="새로 고침", command=self.reload).pack(side="left", padx=3)
        self.reload()

    def reload(self):
        try:
            self.rows = self._load()
        except FileNotFoundError:
            self.rows = []
        except Exception as e:
            messagebox.showerror("읽기 실패", f"keywords.csv 를 읽지 못했어요.\n{e}")
            self.rows = []
        self.cat_box["values"] = known_categories(self.rows)
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            if self.only_todo.get() and r["status"].strip():
                continue
            self.tree.insert("", "end", iid=str(i), values=(r["keyword"], r["memo"], r["category"], r["status"], r.get("link", "")))

    def selected(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def on_select(self, _=None):
        i = self.selected()
        if i is not None:
            r = self.rows[i]
            self.kw.set(r["keyword"])
            self.memo.set(r["memo"])
            self.link.set(r.get("link", ""))
            self.cat.set(r["category"])

    def save(self):
        try:
            self._save(self.rows)
        except PermissionError:
            messagebox.showerror("저장 실패", "keywords.csv 가 엑셀이나 메모장에 열려 있으면 닫고 다시 해 주세요.")
            return False
        self.refresh()
        return True

    def add(self):
        kw = self.kw.get().strip()
        if not kw:
            messagebox.showinfo("키워드", "키워드를 적어 주세요.")
            return
        if any(r["keyword"].replace(" ", "") == kw.replace(" ", "") for r in self.rows):
            if not messagebox.askyesno("이미 있음", f"'{kw}' 은(는) 이미 목록에 있어요. 그래도 추가할까요?"):
                return
        self.rows.append({"keyword": kw, "memo": self.memo.get().strip(), "status": "",
                          "category": self.cat.get().strip(), "link": self.link.get().strip()})
        if self.save():
            self.kw.set("")
            self.memo.set("")
            self.link.set("")

    def update_row(self):
        i = self.selected()
        if i is None:
            messagebox.showinfo("선택", "위 표에서 고칠 줄을 먼저 눌러 주세요.")
            return
        self.rows[i].update(keyword=self.kw.get().strip(), memo=self.memo.get().strip(), category=self.cat.get().strip(),
                            link=self.link.get().strip())
        self.save()

    def reset_status(self):
        i = self.selected()
        if i is not None:
            self.rows[i]["status"] = ""
            self.save()

    def prune(self):
        """아직 안 쓴 줄 중 가사·음원·연예·사람 이슈 키워드, 큰 키워드를 한 번에 지운다 (메모를 적은 줄은 남긴다)"""
        from evergreen_keywords import ISSUE_WORDS
        from trending_keywords import LYRICS
        bad = [r for r in self.rows if not r["status"].strip() and not r["memo"].strip()
               and (LYRICS.search(r["keyword"]) or ISSUE_WORDS.search(r["keyword"]) or "연예" in r["category"])]
        if not bad:
            messagebox.showinfo("정리", "지울 키워드가 없어요.")
            return
        names = ", ".join(r["keyword"] for r in bad[:15]) + (" ..." if len(bad) > 15 else "")
        if messagebox.askyesno("정리", f"아직 안 쓴 키워드 중 {len(bad)}개를 지울까요?\n(가사·음원, 연예·사람 이슈 / 메모 적은 줄은 남겨요)\n\n{names}"):
            self.rows = [r for r in self.rows if r not in bad]
            self.save()

    def delete(self):
        i = self.selected()
        if i is not None and messagebox.askyesno("지우기", f"'{self.rows[i]['keyword']}' 줄을 지울까요?"):
            del self.rows[i]
            self.save()


# ── 글자 색 ──────────────────────────────────────────────────────

def read_style() -> dict:
    import tomllib
    try:
        return tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("style", {})
    except Exception:
        return {}


def write_style_value(key: str, value: str) -> None:
    """config.toml 의 [style] 아래 key 줄만 바꾼다 (없으면 [style] 바로 아래에 넣는다). 다른 줄·설명은 그대로."""
    text = CONFIG.read_text(encoding="utf-8")
    line = f'{key} = "{value}"'
    sec = re.search(r"(?m)^\[style\][^\n]*\n", text)
    if not sec:
        text = text.rstrip("\n") + f"\n\n[style]\n{line}\n"
    else:
        end = re.search(r"(?m)^\[", text[sec.end():])
        body_end = sec.end() + (end.start() if end else len(text) - sec.end())
        body = text[sec.end():body_end]
        pat = re.compile(rf'(?m)^{re.escape(key)}\s*=\s*"[^"]*"')
        body = pat.sub(line, body, count=1) if pat.search(body) else line + "\n" + body
        text = text[:sec.end()] + body + text[body_end:]
    CONFIG.write_text(text, encoding="utf-8")


class ColorTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=14)
        ttk.Label(self, text="색 상자를 눌러 고르면 바로 저장돼요. 다음 글부터 그 색으로 써요.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        style = read_style()
        self.swatches = {}
        for n, (key, label, default) in enumerate(STYLE_KEYS, 1):
            value = style.get(key) or (style.get("heading_color") if key == "quote_color" else None) or default
            ttk.Label(self, text=label, width=22).grid(row=n, column=0, sticky="w", pady=4)
            sw = tk.Label(self, text=value, width=12, bg=value, fg=self._ink(value), relief="groove", cursor="hand2")
            sw.grid(row=n, column=1, sticky="w")
            sw.bind("<Button-1>", lambda _e, k=key: self.pick(k))
            self.swatches[key] = sw
        self.note = ttk.Label(self, foreground="#666", justify="left")
        self.note.grid(row=len(STYLE_KEYS) + 1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.refresh_note()

    @staticmethod
    def palette() -> list[str]:
        try:
            return json.loads((ROOT / "palette.json").read_text(encoding="utf-8"))
        except Exception:
            return []

    def refresh_note(self):
        pal = self.palette()
        if not pal:
            self.note.configure(text="글을 한 번 쓰면 네이버 글자색 목록을 읽어 와서, 다음부터는 그 색들 중에서 고르게 돼요.\n"
                                     "(네이버 목록에 없는 색은 가장 비슷한 색으로 들어가요)")
            return
        off = [lbl for key, lbl, _ in STYLE_KEYS if self.swatches[key].cget("text").lower() not in pal]
        self.note.configure(text="네이버 글자색 목록에 있는 색만 정확히 들어가요."
                                 + (f"\n⚠ 목록에 없는 색: {', '.join(off)} → 가장 비슷한 색으로 들어가요. 다시 골라 주세요." if off else ""))

    @staticmethod
    def _ink(hex_color: str) -> str:
        try:
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return "#000" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#fff"
        except Exception:
            return "#000"

    def pick(self, key: str):
        sw = self.swatches[key]
        pal = self.palette()
        if pal:
            hx = self.pick_from_palette(pal, sw.cget("text"))
        else:
            _, hx = colorchooser.askcolor(color=sw.cget("text"), title="색 고르기")
        if not hx:
            return
        try:
            write_style_value(key, hx.lower())
        except PermissionError:
            messagebox.showerror("저장 실패", "config.toml 이 다른 프로그램에 열려 있으면 닫고 다시 해 주세요.")
            return
        sw.configure(text=hx.lower(), bg=hx, fg=self._ink(hx))
        self.refresh_note()

    def pick_from_palette(self, pal: list[str], current: str) -> str:
        """네이버 글자색 목록과 같은 색 칸을 보여 주고 하나를 고르게 한다"""
        win = tk.Toplevel(self)
        win.title("네이버 글자색 목록에서 고르기")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        chosen = {"v": ""}
        cols = 10
        for i, c in enumerate(pal):
            b = tk.Button(win, bg=c, activebackground=c, width=3, height=1, cursor="hand2",
                          relief="sunken" if c == current.lower() else "raised", bd=3 if c == current.lower() else 1,
                          command=lambda v=c: (chosen.update(v=v), win.destroy()))
            b.grid(row=i // cols, column=i % cols, padx=2, pady=2)
        ttk.Label(win, text="칸을 누르면 바로 저장돼요.", foreground="#666").grid(
            row=len(pal) // cols + 1, column=0, columnspan=cols, pady=6)
        win.wait_window()
        return chosen["v"]


# ── 창 ──────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    try:
        from main import load_config
        name = load_config().get("naver", {}).get("blog_name", "")
    except Exception:
        name = ""
    from version import VERSION
    root.title(f"{name or '네이버 블로그'} 자동화  (버전 {VERSION})")
    root.geometry("940x640")
    try:
        ttk.Style().theme_use("vista" if os.name == "nt" else "clam")
    except Exception:
        pass

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    run = ttk.Frame(nb, padding=12)
    nb.add(run, text="  실행  ")
    shop = is_shop()
    for col, (group, items) in enumerate(BUTTONS):
        if shop:  # 쇼핑 블로그는 키워드를 직접 넣는다 (이슈·정보 키워드 찾기 버튼은 숨김)
            items = [it for it in items if it[1] not in SHOP_HIDDEN]
        lf = ttk.LabelFrame(run, text=group, padding=10)
        lf.grid(row=0, column=col, sticky="nsew", padx=6)
        run.columnconfigure(col, weight=1)
        for label, bat, tip in items:
            ttk.Button(lf, text=label, command=lambda b=bat: run_bat(b)).pack(fill="x", pady=(6, 0))
            ttk.Label(lf, text=tip, foreground="#777", wraplength=250).pack(fill="x")
    bottom = ttk.Frame(run)
    bottom.grid(row=1, column=0, columnspan=3, sticky="w", pady=16, padx=6)
    ttk.Button(bottom, text="output 폴더 열기", command=lambda: open_path(ROOT / "output")).pack(side="left", padx=3)
    ttk.Button(bottom, text="설정 파일(config.toml) 열기", command=lambda: open_path(CONFIG)).pack(side="left", padx=3)
    ttk.Label(run, text="버튼을 누르면 검은 창이 새로 떠요. 거기서 번호 입력·엔터를 하면 되고, 끝나면 창을 닫아도 돼요.",
              foreground="#555").grid(row=2, column=0, columnspan=3, sticky="w", padx=6)

    kw_tab = KeywordTab(nb)
    nb.add(kw_tab, text="  키워드 목록  ")
    nb.add(ColorTab(nb), text="  글자 색  ")
    # 키워드 찾기 창에서 넣은 것이 보이도록, 탭을 열 때마다 다시 읽는다
    nb.bind("<<NotebookTabChanged>>", lambda _e: kw_tab.reload() if nb.select() == str(kw_tab) else None)
    root.mainloop()


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    main()
