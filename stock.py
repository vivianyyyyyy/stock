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
MENU_OUT = os.path.join(os.path.dirname(OUT), "menu.html")
ADMIN_OUT = os.path.join(os.path.dirname(OUT), "admin.html")


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


def header_price(h):
    """表头里的单价，例如「Aura 2,500円」-> 2500；没写就返回 None。"""
    m = re.search(r"([\d,]+)\s*円", re.sub(r"\s+", " ", h))
    return int(m.group(1).replace(",", "")) if m and m.group(1).replace(",", "") else None


def parse_header(h):
    h = re.sub(r"\s+", " ", h)  # 表头里有全角空格、窄空格等，统一成普通空格
    h = re.sub(r"\s*[\d,]+\s*円", "", h).strip()
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
    "ミニスローガンキーホルダー": "小手幅",
    "ミニうちわキーホルダー": "团扇钥匙链",
    SHARED_CAT: "小手幅/团扇钥匙链",
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
            cols.append((i, it, fix.get(f"{name}|{it}", c), header_price(h)))
    items = {}
    for _, it, c, price in cols:
        items.setdefault((c, it), {"form": name, "category": c, "item": it, "by_date": {}, "price": None})
        if price:
            items[(c, it)]["price"] = price
    orders, notes = 0, []
    for r in rows[1:]:
        if not any(x.strip() for x in r):
            continue
        orders += 1
        d = norm_date(r[di]) if di < len(r) else "未填"
        for i, it, c, _ in cols:
            v = r[i].strip() if i < len(r) else ""
            nums = [int(float(x)) for x in re.findall(r"\d+(?:\.\d+)?", v)]
            q = max(nums) if nums else 0
            if len(nums) > 1 or (v and not nums):
                notes.append(f"表格第 {rows.index(r) + 1} 行（{r[0]} 提交）的「{c} {it}」填的是「{v}」，现在按 {q} 件算。确认后可以直接在表格里改成正确数字")
            if q:
                bd = items[(c, it)]["by_date"]
                bd[d] = bd.get(d, 0) + q
    imgs = products.get("images", {})
    prices = products.get("prices", {})
    for (c, it), v in items.items():
        v["price"] = (prices.get(f"{name}|{c}|{it}") or prices.get(f"{name}|{it}") or v["price"]
                      or prices.get(f"{name}|{c}") or prices.get(f"{name}|{CAT_ZH.get(c, c)}"))
        v["category"] = CAT_ZH.get(c, c)
        v["img"] = imgs.get(f"{name}|{c}|{it}") or imgs.get(f"{name}|{it}")
    for i, n in enumerate(notes):
        for jp, zh in CAT_ZH.items():
            n = n.replace(f"「{jp} ", f"「{zh} ")
        notes[i] = n
    return {"form": name, "orders": orders, "items": list(items.values()), "notes": notes}


def build(data, tpl_name="template.html", slot="/*__DATA__*/null"):
    tpl = open(os.path.join(os.path.dirname(OUT), "..", tpl_name), encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace(slot, payload)


def menu_data(forms, products):
    """给客人看的菜单：只有款式、分类、价格、图片，没有订单数。"""
    cfg = products.get("menu", {})
    names, hide = cfg.get("names", {}), set(cfg.get("hide", []))
    items = []
    for f in forms:
        for it in f["items"]:
            k = f"{f['form']}|{it['category']}|{it['item']}"
            if k in hide:
                continue
            items.append({"form": f["form"], "category": it["category"], "item": it["item"],
                          "name": names.get(k, it["item"]), "price": it.get("price"), "img": it.get("img")})
    return {"forms": [f["form"] for f in forms], "form_names": cfg.get("form_names", {}), "items": items}


def write_if_changed(path, html):
    if unchanged(html, path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return True


def unchanged(html, path=OUT):
    """和上次生成的网页比，除了更新时间以外是否完全一样（数据和模板都算）。"""
    try:
        old = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return False
    strip = lambda t: re.sub(r'"updated": "[^"]*"', "", t)
    return strip(old) == strip(html)


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
    # 有表格没读到时不动菜单，免得菜单里少了款式
    if not errors:
        menu = menu_data(forms, products)
        for tpl, out in (("menu_template.html", MENU_OUT), ("admin_template.html", ADMIN_OUT)):
            if write_if_changed(out, build(menu, tpl, "/*__MENU__*/null")):
                print(f"已生成 {out}")
    if not write_if_changed(OUT, build(data)):
        print("数据和模板都没有变化，不改网页")
        return
    for e in errors:
        print("警告：" + e, file=sys.stderr)
    print(f"已生成 {OUT}（{now}）")


if __name__ == "__main__":
    main()
