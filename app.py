import os, json, hmac, hashlib, sqlite3, time, urllib.request, urllib.parse
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response

# ====== SOZLAMALAR (faqat shu yerni o'zgartiring) ======
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKENNI_SHU_YERGA")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))      # sizning Telegram ID (@userinfobot dan oling)
CARD, CARDOWN = "KARTA RAQAMI", "KARTA EGASI ISMI"
PLANS = {  # id: (nomi, narx, kun, o'rin)
    "m": ("1 oylik", 50000, 30, 0),
    "p10": ("10 ta yo'lovchi", 15000, 0, 10),
    "p20": ("20 ta yo'lovchi", 25000, 0, 20),
}
# =======================================================
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "surxongo.db")
app = Flask(__name__)

def q(sql, a=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    cur = c.execute(sql, a); r = [dict(x) for x in cur.fetchall()]
    c.commit(); c.close(); return r
def q1(sql, a=()):
    r = q(sql, a); return r[0] if r else None
def now(): return (datetime.utcnow() + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")

c = sqlite3.connect(DB)
c.executescript("""
CREATE TABLE IF NOT EXISTS trips(id INTEGER PRIMARY KEY, owner INT, name, phone, car, src, dst, date, time,
 price INT, seats INT, booked INT DEFAULT 0, hidden INT DEFAULT 0, ts INT);
CREATE TABLE IF NOT EXISTS drivers(uid INT PRIMARY KEY, bought INT DEFAULT 0, expires TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS pays(id INTEGER PRIMARY KEY, uid INT, name, phone, plan, price INT, status, ts INT);
"""); c.close()

def auth(init):
    d = dict(urllib.parse.parse_qsl(init, keep_blank_values=True))
    h = d.pop("hash", "")
    s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(key, s.encode(), hashlib.sha256).hexdigest(), h): return None
    if time.time() - int(d.get("auth_date", 0)) > 86400: return None
    return json.loads(d["user"])

def notify(uid, text):
    try:
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % BOT_TOKEN,
            urllib.parse.urlencode({"chat_id": uid, "text": text}).encode(), timeout=5)
    except Exception: pass

def state(uid):
    n = now()
    dr = q1("SELECT * FROM drivers WHERE uid=?", (uid,)) or {"bought": 0, "expires": ""}
    used = q1("SELECT COALESCE(SUM(CASE WHEN hidden=1 OR date||'T'||time<? THEN booked ELSE seats END),0) u "
              "FROM trips WHERE owner=?", (n, uid))["u"]
    month = bool(dr["expires"]) and dr["expires"] >= n[:10]
    rem = dr["bought"] - used
    return {"month": month, "expires": dr["expires"], "rem": rem, "ok": month or rem > 0}

LIVE = "hidden=0 AND booked<seats AND date||'T'||time>?"

@app.post("/api")
def api():
    j = request.get_json(force=True)
    u = auth(j.get("initData", ""))
    if not u: return jsonify(err="Iltimos, Telegram ichidan oching"), 401
    uid, adm, a, n = u["id"], u["id"] == ADMIN_ID, j.get("act"), now()
    try:
        if a == "add":
            s = state(uid)
            if not s["ok"]: return jsonify(err="Avval tarif to'lang")
            f = {k: str(j.get(k, "")).strip() for k in ("src", "dst", "date", "time", "name", "phone", "car")}
            price, seats = int(j.get("price", 0)), max(1, min(8, int(j.get("seats", 1))))
            if f["src"] == f["dst"] or not all([f["date"], f["time"], f["name"], f["phone"]]) or price <= 0:
                return jsonify(err="Maydonlarni to'g'ri to'ldiring")
            if not s["month"]: seats = min(seats, s["rem"])
            q("INSERT INTO trips(owner,name,phone,car,src,dst,date,time,price,seats,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (uid, f["name"], f["phone"], f["car"], f["src"], f["dst"], f["date"], f["time"], price, seats, int(time.time())))
        elif a == "book":
            t = q1("SELECT * FROM trips WHERE id=? AND " + LIVE, (j["id"], n))
            if not t: return jsonify(err="Joy qolmadi yoki vaqti o'tgan")
            if t["owner"] == uid: return jsonify(err="O'z qatnovingizni band qila olmaysiz")
            nb = t["booked"] + 1
            q("UPDATE trips SET booked=?, hidden=? WHERE id=? AND booked=?", (nb, int(nb >= t["seats"]), t["id"], t["booked"]))
            who = ("@" + u["username"]) if u.get("username") else u.get("first_name", "yo'lovchi")
            notify(t["owner"], "🚕 %s → %s (%s %s)\nYangi yo'lovchi: %s\nQolgan joy: %d" % (t["src"], t["dst"], t["date"], t["time"], who, t["seats"] - nb))
        elif a == "del":
            q("UPDATE trips SET hidden=1 WHERE id=? AND owner=?", (j["id"], uid))
        elif a == "pay":
            p = PLANS[j["plan"]]
            q("INSERT INTO pays(uid,name,phone,plan,price,status,ts) VALUES(?,?,?,?,?,'pending',?)",
              (uid, str(j["name"])[:60], str(j["phone"])[:20], j["plan"], p[1], int(time.time())))
            notify(ADMIN_ID, "💰 Yangi to'lov so'rovi: %s (%s) — %s, %d so'm" % (j["name"], j["phone"], p[0], p[1]))
        elif a in ("approve", "reject"):
            if not adm: return jsonify(err="Ruxsat yo'q"), 403
            p = q1("SELECT * FROM pays WHERE id=? AND status='pending'", (j["id"],))
            if p and a == "approve":
                pl = PLANS[p["plan"]]
                q("INSERT OR IGNORE INTO drivers(uid) VALUES(?)", (p["uid"],))
                if pl[2]:
                    cur = q1("SELECT expires FROM drivers WHERE uid=?", (p["uid"],))["expires"] or n[:10]
                    base = max(cur, n[:10])
                    new = (datetime.strptime(base, "%Y-%m-%d") + timedelta(days=pl[2])).strftime("%Y-%m-%d")
                    q("UPDATE drivers SET expires=? WHERE uid=?", (new, p["uid"]))
                else:
                    q("UPDATE drivers SET bought=bought+? WHERE uid=?", (pl[3], p["uid"]))
                notify(p["uid"], "✅ To'lovingiz tasdiqlandi: %s. Endi e'lon qo'shishingiz mumkin." % pl[0])
            if p: q("UPDATE pays SET status=? WHERE id=?", ("approved" if a == "approve" else "rejected", j["id"]))
    except Exception as e:
        return jsonify(err="Xatolik: %s" % e)
    return jsonify(
        err=None, me=uid, admin=adm, state=state(uid), card=CARD, cardown=CARDOWN,
        plans=[{"id": k, "name": v[0], "price": v[1]} for k, v in PLANS.items()],
        trips=q("SELECT * FROM trips WHERE " + LIVE + " ORDER BY date,time", (n,)),
        mine=q("SELECT * FROM trips WHERE owner=? AND date||'T'||time>? AND hidden=0 ORDER BY date,time", (uid, n)),
        pays=q("SELECT * FROM pays WHERE uid=? ORDER BY id DESC LIMIT 10", (uid,)),
        pending=q("SELECT * FROM pays WHERE status='pending' ORDER BY id") if adm else [])

@app.get("/")
def index(): return Response(HTML, mimetype="text/html")

HTML = r"""<!DOCTYPE html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Surxon GO</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:var(--tg-theme-secondary-bg-color,#f4f5f7);--card:var(--tg-theme-bg-color,#fff);--tx:var(--tg-theme-text-color,#111827);--mu:var(--tg-theme-hint-color,#6b7280);--ac:#ffc400;--bd:rgba(128,128,128,.25);--ok:#059669;--rd:#dc2626}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.4 system-ui,sans-serif}
.w{max-width:560px;margin:0 auto;padding:0 12px 24px}
.hd{background:linear-gradient(135deg,#111827,#1f2937);color:#fff;margin:0 -12px 14px;padding:20px 16px 30px;border-radius:0 0 22px 22px}
.hd h1{font-size:22px;margin:0}.hd p{margin:4px 0 0;color:#fcd34d;font-size:13px}
.tabs{display:flex;gap:4px;background:var(--card);padding:4px;border-radius:14px;margin:-36px 0 14px;position:relative;box-shadow:0 4px 14px rgba(0,0,0,.15)}
.tabs button{flex:1;padding:11px 2px;border:0;background:none;color:var(--mu);border-radius:10px;font-weight:700;font-size:14px}
.tabs button.on{background:var(--ac);color:#111}
.card{background:var(--card);border-radius:16px;padding:14px;margin-bottom:12px}
.rt{display:flex;align-items:center;gap:8px;background:var(--bg);border-radius:14px;padding:12px}
.stop{max-width:38%}.stop small{display:block;font-size:11px;color:var(--mu);text-transform:uppercase}
.stop b{font-size:18px;line-height:1.15}.stop.r{text-align:right;margin-left:auto}
.mid{flex:1;display:flex;align-items:center;min-width:30px}.mid:before,.mid:after{content:"";flex:1;border-top:2px dashed var(--mu);opacity:.5}
.meta{display:flex;justify-content:space-between;align-items:center;margin-top:12px}
.chip{background:var(--bg);border-radius:20px;padding:4px 10px;font-size:13px;font-weight:700;margin-right:6px}
.pr{font-size:20px;font-weight:800}.pr small,.mu{color:var(--mu);font-size:12px;font-weight:600}
.drv{display:flex;align-items:center;gap:10px;margin:12px 0;padding-top:12px;border-top:1px dashed var(--bd)}
.av{width:36px;height:36px;border-radius:50%;background:var(--ac);color:#111;display:flex;align-items:center;justify-content:center;font-weight:800}
.seats{margin-left:auto;font-size:12px;font-weight:700;padding:4px 10px;border-radius:20px;background:#d1fae5;color:var(--ok)}
.act{display:flex;gap:8px}
.b{flex:1;padding:12px;border:0;border-radius:12px;background:var(--ac);color:#111;font-weight:800;font-size:15px;text-align:center;text-decoration:none}
.b.g{background:#111827;color:#fff}.b.x{background:none;color:var(--rd);border:1px solid var(--bd)}.b.ok{background:var(--ok);color:#fff}.b:disabled{opacity:.4}
select,input{width:100%;padding:12px;margin:4px 0 8px;border:1px solid var(--bd);border-radius:12px;background:var(--bg);color:var(--tx);font-size:15px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:8px}.sec{font-weight:800;margin:6px 0 10px;font-size:16px}
.empty{text-align:center;color:var(--mu);padding:30px 10px}
.st{border-radius:12px;padding:10px 12px;font-weight:700;margin-bottom:10px;background:#d1fae5;color:var(--ok)}.st.no{background:#fee2e2;color:var(--rd)}
.plan{display:flex;justify-content:space-between;align-items:center;border:2px solid var(--bd);border-radius:14px;padding:12px;margin-bottom:8px}
.plan.on{border-color:var(--ac);background:rgba(255,196,0,.12)}.plan b{display:block}
.cn{font-size:20px;font-weight:800;text-align:center;background:var(--bg);border-radius:12px;padding:12px;margin:8px 0}
.hide{display:none}
</style></head><body><div class="w">
<div class="hd"><h1>🚕 Surxon GO</h1><p>Tumanlararo qatnovlar · tez va qulay</p></div>
<div class="tabs"><button id="t1" class="on">Yo'lovchi</button><button id="t2">Haydovchi</button><button id="t3">Tarif</button><button id="t4" class="hide">Admin</button></div>
<div id="p1"><div class="g2"><select id="fa"></select><select id="fb"></select></div><div id="list">Yuklanmoqda...</div></div>
<div id="p2" class="hide"><div id="status"></div>
 <div class="card" id="form"><div class="sec">Yangi qatnov</div>
  <div class="g2"><select id="a"></select><select id="b"></select></div>
  <div class="g2"><input id="d" type="date"><input id="tm" type="time"></div>
  <div class="g2"><input id="pr" type="number" placeholder="Narx (so'm)"><input id="st" type="number" min="1" max="8" value="3" placeholder="Joy"></div>
  <input id="nm" placeholder="Ismingiz"><input id="ph" type="tel" placeholder="Telefon: 90 123 45 67"><input id="car" placeholder="Mashina (Nexia 3, oq)">
  <button class="b" id="add" style="width:100%">Qo'shish</button></div>
 <div class="sec">Mening qatnovlarim</div><div id="mine"></div></div>
<div id="p3" class="hide"><div class="sec">Tarifni tanlang</div><div id="plans"></div>
 <div class="card"><div class="mu">Tarifni tanlang, quyidagi kartaga Click yoki Payme orqali pul o'tkazing va "To'lov qildim" ni bosing. Admin tasdiqlagach tarif yoqiladi.</div>
  <div class="cn" id="cn"></div><div class="mu" style="text-align:center" id="co"></div>
  <input id="pn" placeholder="Ismingiz" style="margin-top:10px"><input id="pp" type="tel" placeholder="Telefon">
  <button class="b ok" id="paid" style="width:100%">To'lov qildim</button></div>
 <div class="sec">To'lovlarim</div><div id="mypay"></div></div>
<div id="p4" class="hide"><div class="sec">Kutilayotgan to'lovlar</div><div id="adm"></div></div>
</div><script>
const tg=Telegram.WebApp;tg.ready();tg.expand();
const D=["Termiz","Denov","Sho'rchi","Boysun","Qumqo'rg'on","Jarqo'rg'on","Angor","Oltinsoy","Sariosiyo","Uzun","Muzrabot","Sherobod","Bandixon","Qiziriq","Shartoq"];
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=n=>Number(n).toLocaleString('ru-RU').replace(/\u00a0/g,' ');
const tel=p=>{let d=String(p).replace(/\D/g,'');if(d.length==9)d='998'+d;return '+'+d};
const show=p=>tel(p).replace(/^\+998(\d{2})(\d{3})(\d{2})(\d{2})$/,'+998 $1 $2 $3 $4');
const o=(el,all)=>el.innerHTML=(all?'<option value="">Hammasi</option>':'')+D.map(x=>`<option>${x}</option>`).join('');
o($('fa'),1);o($('fb'),1);o($('a'));o($('b'));$('b').selectedIndex=1;$('d').value=new Date().toISOString().slice(0,10);
let S={},plan='m';
function tab(n){for(let i=1;i<=4;i++){$('p'+i).classList.toggle('hide',i!=n);$('t'+i).classList.toggle('on',i==n)}}
[1,2,3,4].forEach(i=>$('t'+i).onclick=()=>tab(i));
async function api(act,d={}){try{const r=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg.initData,act,...d})});return await r.json()}catch(e){return{err:'Internet yo\'q'}}}
async function run(act,d){const r=await api(act,d);if(r.err)tg.showAlert(r.err);else{S=r;render()}return r}
const sh=d=>d.slice(8)+'.'+d.slice(5,7);
function card(t,mine){const l=t.seats-t.booked;
return `<div class="card"><div class="rt"><div class="stop"><small>Qayerdan</small><b>${esc(t.src)}</b></div><div class="mid"><span style="padding:0 6px;font-size:20px">🚕</span></div><div class="stop r"><small>Qayerga</small><b>${esc(t.dst)}</b></div></div>
<div class="meta"><div><span class="chip">📅 ${sh(t.date)}</span><span class="chip">🕗 ${esc(t.time)}</span></div><div class="pr">${fmt(t.price)}<small> so'm</small></div></div>
<div class="drv"><div class="av">${esc((t.name||'?')[0].toUpperCase())}</div><div><b>${esc(t.name)}</b><div class="mu">${esc(t.car)}</div></div><span class="seats">${l} ta joy</span></div>
<div class="act">${mine?`<button class="b x" data-del="${t.id}">O'chirish</button>`:`<a class="b g" href="tel:${tel(t.phone)}">📞 ${show(t.phone)}</a><button class="b" style="flex:.7" data-bk="${t.id}">Band qilish</button>`}</div></div>`}
function render(){
 const fa=$('fa').value,fb=$('fb').value,f=S.trips.filter(t=>(!fa||t.src==fa)&&(!fb||t.dst==fb));
 $('list').innerHTML=f.length?f.map(t=>card(t,0)).join(''):'<div class="empty"><div style="font-size:34px">🚕</div>Hozircha qatnov yo\'q</div>';
 $('mine').innerHTML=S.mine.length?S.mine.map(t=>card(t,1)).join(''):'<div class="empty">Hali qo\'shmagansiz</div>';
 const s=S.state;
 $('status').innerHTML=s.month?`<div class="st">✅ 1 oylik tarif: ${s.expires} gacha</div>`:s.rem>0?`<div class="st">✅ Qolgan o'rinlar: ${s.rem} ta</div>`:`<div class="st no">❗ Tarif yo'q. "Tarif" bo'limidan to'lov qiling.</div>`;
 $('form').classList.toggle('hide',!s.ok);
 $('plans').innerHTML=S.plans.map(p=>`<div class="plan ${p.id==plan?'on':''}" data-pl="${p.id}"><b>${esc(p.name)}</b><div class="pr">${fmt(p.price)}<small> so'm</small></div></div>`).join('');
 $('cn').textContent=S.card;$('co').textContent=S.cardown;
 const lab={pending:'⏳ Kutilmoqda',approved:'✅ Tasdiqlandi',rejected:'❌ Rad etildi'};
 const pn=id=>(S.plans.find(p=>p.id==id)||{}).name;
 $('mypay').innerHTML=S.pays.length?S.pays.map(p=>`<div class="card"><b>${esc(pn(p.plan))}</b> · ${fmt(p.price)} so'm<div class="mu">${lab[p.status]}</div></div>`).join(''):'<div class="empty">To\'lov yo\'q</div>';
 $('t4').classList.toggle('hide',!S.admin);
 $('adm').innerHTML=S.pending.length?S.pending.map(p=>`<div class="card"><b>${esc(p.name)}</b> · ${esc(show(p.phone))}<div class="mu">${esc(pn(p.plan))} · ${fmt(p.price)} so'm</div><div class="act" style="margin-top:10px"><button class="b ok" data-ap="${p.id}">Tasdiqlash</button><button class="b x" data-rj="${p.id}">Rad</button></div></div>`).join(''):'<div class="empty">Kutilayotgan to\'lov yo\'q</div>';
}
$('fa').onchange=$('fb').onchange=()=>S.trips&&render();
document.addEventListener('click',e=>{const x=e.target.closest('[data-pl],[data-bk],[data-del],[data-ap],[data-rj]');if(!x)return;const d=x.dataset;
 if(d.pl){plan=d.pl;render()}else if(d.bk)run('book',{id:+d.bk}).then(r=>!r.err&&tg.showAlert('Band qilindi! Haydovchiga qo\'ng\'iroq qiling.'));
 else if(d.del)tg.showConfirm("O'chirilsinmi?",y=>y&&run('del',{id:+d.del}));
 else if(d.ap)run('approve',{id:+d.ap});else if(d.rj)run('reject',{id:+d.rj})});
$('add').onclick=()=>{const v=k=>$(k).value.trim();run('add',{src:v('a'),dst:v('b'),date:v('d'),time:v('tm'),price:+v('pr'),seats:+v('st'),name:v('nm'),phone:v('ph'),car:v('car')}).then(r=>!r.err&&tg.showAlert("Qo'shildi ✅"))};
$('paid').onclick=()=>{if(!$('pn').value.trim()||!$('pp').value.trim())return tg.showAlert('Ism va telefonni kiriting');run('pay',{plan,name:$('pn').value.trim(),phone:$('pp').value.trim()}).then(r=>!r.err&&tg.showAlert('Yuborildi. Admin tasdiqlashini kuting ✅'))};
const first=tg.initDataUnsafe?.user?.first_name;if(first){$('nm').value=$('pn').value=first}
async function load(){const r=await api('list');if(r.err){$('list').innerHTML=esc(r.err);return}S=r;render()}
load();setInterval(load,30000);
</script></body></html>"""
