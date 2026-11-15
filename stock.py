"""读取 Google 表单回复表，生成备货清单网页 docs/index.html。

表格 ID 从环境变量读取：SHEET_ED95、SHEET_K2、SHEET_UNIT。
网页里只放汇总数字，不放顾客邮箱、昵称和备注。
"""
import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

FORMS = [("ed95", "SHEET_ED95"), ("k2", "SHEET_K2"), ("unit", "SHEET_UNIT")]
CATS = ["ミニスローガンキーホルダー", "ミニうちわキーホルダー", "スローガン", "うちわ"]
SHARED_CAT = "ミニキーホルダー"
DATE_COL = "受取可能の日程PICK-UP DATE"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")


def fetch_csv(sheet_id):
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    if text.lstrip().startswith("<"):
        raise RuntimeError("拿到的是登录页，表格可能没有设成「知道链接的任何人可查看」")
    return list(csv.reader(io.StringIO(text)))


def as_cat(s):
    s = re.sub(r"\s+", "", s).replace("キーホルダ", "キーホルダー").replace("ーー", "ー")
    if "/" in s and "キーホルダー" in s:
        return SHARED_CAT
    return s if s in CATS else None


def split_item(text):
    """'black スローガン' -> ('black', 'スローガン')"""
    text = text.strip()
    for c in CATS:
        if re.sub(r"\s+", "", text).endswith(c) and text != c:
            head = re.sub(r"[\s　]*" + "".join(ch + r"\s*" for ch in c[:-1]) + c[-1] + r"$", "", text)
            return head.strip(), c
    return None


def parse_header(h):
    h = re.sub(r"[\s　]*[\d,]+円", "", h).replace("　", " ").strip()
    m = re.match(r"^(.*?)\s*\[(.*)\]\s*$", h)
    outside, inside = (m.group(1).strip(), m.group(2).strip()) if m else (h, "")
    if inside == "個数":
        inside = ""
    if " - " in outside and not inside:
        item, cat = outside.rsplit(" - ", 1)
        return item.strip(), as_cat(cat) or cat.strip()
    if outside and inside:
        if as_cat(outside):
            return inside, as_cat(outside)
        if as_cat(inside):
            return outside, as_cat(inside)
        return f"{outside} {inside}", "其他"
    text = outside or inside
    return split_item(text) or (text, "其他")


def norm_date(s):
    m = re.search(r"(\d+)月(\d+)日\s*[\(（]?([^\)）]*)", s)
    if m:
        wd = f"（{m.group(3).strip()}）" if m.group(3).strip() else ""
        return f"{int(m.group(1))}/{int(m.group(2))}{wd}"
    return s.strip() or "未填"


CAT_ZH = {
    "スローガン": "手幅",
    "うちわ": "团扇",
    "ミニスローガンキーホルダー": "小手幅挂件",
    "ミニうちわキーホルダー": "小团扇挂件",
    SHARED_CAT: "小挂件（手幅/团扇）",
}


def load_products():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "products.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        return {}


def parse_form(name, rows, products):
    hdr = rows[0]
    di = hdr.index(DATE_COL)
    fix = products.get("category", {})
    cols = []
    for i, h in enumerate(hdr):
        if 1 < i < di:
            it, c = parse_header(h)
            cols.append((i, it, fix.get(f"{name}|{it}", c)))
    items = {(c, it): {"form": name, "category": c, "item": it, "by_date": {}} for _, it, c in cols}
    orders, notes = 0, []
    for r in rows[1:]:
        if not any(x.strip() for x in r):
            continue
        orders += 1
        d = norm_date(r[di]) if di < len(r) else "未填"
        for i, it, c in cols:
            v = r[i].strip() if i < len(r) else ""
            nums = [int(float(x)) for x in re.findall(r"\d+(?:\.\d+)?", v)]
            q = max(nums) if nums else 0
            if len(nums) > 1 or (v and not nums):
                notes.append(f"第 {orders} 张订单的「{c} {it}」填的是「{v}」，按 {q} 件计算，请核对")
            if q:
                bd = items[(c, it)]["by_date"]
                bd[d] = bd.get(d, 0) + q
    imgs = products.get("images", {})
    for (c, it), v in items.items():
        v["category"] = CAT_ZH.get(c, c)
        v["img"] = imgs.get(f"{name}|{c}|{it}") or imgs.get(f"{name}|{it}")
    for i, n in enumerate(notes):
        for jp, zh in CAT_ZH.items():
            n = n.replace(f"「{jp} ", f"「{zh} ")
        notes[i] = n
    return {"form": name, "orders": orders, "items": list(items.values()), "notes": notes}


def unchanged(data):
    """和上次生成的网页比，除了时间以外数据是否一样。"""
    try:
        old = open(OUT, encoding="utf-8").read()
        m = re.search(r"const DATA = (.*?);\n", old)
        prev = json.loads(m.group(1).replace("<\\/", "</"))
    except Exception:
        return False
    return {k: v for k, v in prev.items() if k != "updated"} == {k: v for k, v in data.items() if k != "updated"}


def main():
    forms, errors = [], []
    products = load_products()
    for name, env in FORMS:
        sid = os.environ.get(env, "").strip()
        if not sid:
            errors.append(f"{name}：没有设置 {env}")
            continue
        try:
            forms.append(parse_form(name, fetch_csv(sid), products))
        except Exception as e:  # 一张表出错不影响其它表
            errors.append(f"{name}：{e}")
    if not forms:
        sys.exit("三张表都没读到：" + "；".join(errors))
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    data = {"updated": now, "forms": forms, "errors": errors}
    if unchanged(data):
        print("数据没有变化，不改网页")
        return
    tpl = open(os.path.join(os.path.dirname(OUT), "..", "template.html"), encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*__DATA__*/null", payload))
    for e in errors:
        print("警告：" + e, file=sys.stderr)
    print(f"已生成 {OUT}（{now}）")


if __name__ == "__main__":
    main()
