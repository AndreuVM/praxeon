"""Construye la hoja de revisión humana (HTML local, sin servidor) con ~300 casos priorizados por desacuerdo.

Entrada: data/finetune/real_cases.jsonl + teacher_real.jsonl (+ synthetic_*.jsonl con profesor opcional).
Salida: data/finetune/review.html. Las decisiones se guardan en el navegador y se exportan a JSON (botón «Exportar»).
Los datos son tuyos y redactados; el archivo no sale del equipo.
"""

from __future__ import annotations

import argparse
import html
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

POLICY_TO_DECISION = {"ALLOW": "ALLOW", "BLOCK": "BLOCK", "REVIEW": "ABSTAIN", "REPLAN": "REPLAN", "ABSTAIN": "ABSTAIN"}


def read(path):
    p = Path(path)
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()] if p.exists() else []


def build_items(real, teacher, n, seed):
    by_id = {t["id"]: t for t in teacher if "label" in t}
    items = []
    for r in real:
        t = by_id.get(r["id"])
        if not t:
            continue
        policy = POLICY_TO_DECISION.get(str(r["observed"]["policy_status"]), "ALLOW")
        teach = t["label"]["decision"]
        score = 0
        if teach != policy:
            score += 3                                         # profesor y política discrepan: lo más informativo
        if r["observed"].get("approved_by_human") is not None:
            score += 2                                         # hubo una persona en el bucle
        if teach in ("BLOCK", "ABSTAIN") or policy in ("BLOCK", "ABSTAIN"):
            score += 2                                         # clases raras
        if t["label"]["confidence"] < 0.6:
            score += 1
        items.append({"id": r["id"], "score": score, "policy": policy, "teacher": teach, "teacher_reason": t["label"]["reason"],
                      "teacher_conf": t["label"]["confidence"], "is_loop": t["label"]["is_loop"], "is_grounded": t["label"]["is_grounded"],
                      "progress": t["label"]["progress"], "goal": r["goal"], "tool": r["tool"], "arguments": r["arguments"],
                      "state": r["state"], "human": r["observed"].get("approved_by_human"), "success": r["observed"].get("success"),
                      "output": r["observed"].get("output", "")[:300]})
    rng = random.Random(seed)
    rng.shuffle(items)
    items.sort(key=lambda x: -x["score"])
    top = items[: int(n * 0.8)]
    rest = items[int(n * 0.8):]
    top += rng.sample(rest, min(len(rest), n - len(top)))      # 20 % de coincidencias, para medir al profesor
    rng.shuffle(top)
    return top


PAGE = """<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Revisión de casos</title>
<style>
:root{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--card:#fff;--line:#e7e5e4;--acc:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#f5f5f4;--mut:#a8a29e;--card:#1c1b1a;--line:#2e2c2a;--acc:#6ea0ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:16px}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;color:var(--mut);margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:12px}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;font:13px/1.45 ui-monospace,Consolas,monospace;max-height:300px;overflow:auto}
.act{font:14px ui-monospace,Consolas,monospace;font-weight:600;word-break:break-all}
.chips span{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:1px 10px;margin:2px 4px 2px 0;font-size:13px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:8px 14px;cursor:pointer;min-height:40px}
button.sel{background:var(--acc);color:#fff;border-color:var(--acc)} button:focus-visible{outline:2px solid var(--acc)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px} .mut{color:var(--mut);font-size:13px}
</style>
<main>
<div class="bar"><b id="pos"></b><span id="done"></span><button id="prev">← Anterior</button><button id="next">Siguiente →</button><button id="exp">Exportar JSON</button></div>
<div class="mut">Atajos: <b>A</b> permitir · <b>R</b> replanificar · <b>B</b> bloquear · <b>S</b> revisión humana · <b>←/→</b> moverse. Sin marcar = no revisado.</div>
<div id="card" class="card"></div>
</main>
<script>
const ITEMS=__ITEMS__;let i=0;const KEY="laya-review-v1";
let ans={};try{ans=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(ans))}catch(e){}};
const $=s=>document.querySelector(s);const esc=s=>String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const LAB={ALLOW:"Permitir (A)",REPLAN:"Replanificar (R)",BLOCK:"Bloquear (B)",ABSTAIN:"Revisión humana (S)"};
function render(){const it=ITEMS[i];const a=ans[it.id]||{};
$("#pos").textContent=`${i+1} / ${ITEMS.length}`;$("#done").textContent=`revisados: ${Object.keys(ans).length}`;
$("#card").innerHTML=`<div class="mut">Objetivo</div><div>${esc(it.goal)}</div>
<div class="mut" style="margin-top:10px">Acción propuesta</div><div class="act">${esc(it.tool)} ${esc(JSON.stringify(it.arguments))}</div>
<div class="chips" style="margin-top:8px"><span>política real: <b>${it.policy}</b></span><span>profesor: <b>${it.teacher}</b> (${it.teacher_conf})</span>${it.human===null?"":`<span>persona: ${it.human?"aprobó":"denegó"}</span>`}${it.success===null?"":`<span>ejecución: ${it.success?"ok":"falló"}</span>`}</div>
<div class="mut">Motivo del profesor</div><div>${esc(it.teacher_reason)}</div>
<details style="margin-top:10px"><summary class="mut">Estado completo y resultado</summary><pre>${esc(it.state)}</pre><pre>${esc(it.output)}</pre></details>
<div class="row">${Object.keys(LAB).map(k=>`<button data-k="${k}" class="${a.decision===k?"sel":""}">${LAB[k]}</button>`).join("")}</div>
<div class="row"><label class="mut"><input type="checkbox" id="loop" ${a.is_loop??it.is_loop?"checked":""}> repetición exacta</label>
<label class="mut"><input type="checkbox" id="gr" ${a.is_grounded??it.is_grounded?"checked":""}> fundamentada</label></div>`;
document.querySelectorAll("[data-k]").forEach(b=>b.onclick=()=>set(b.dataset.k));}
function set(k){const it=ITEMS[i];ans[it.id]={decision:k,is_loop:$("#loop").checked,is_grounded:$("#gr").checked,teacher:it.teacher,policy:it.policy};save();if(i<ITEMS.length-1)i++;render();}
const go=d=>{i=Math.max(0,Math.min(ITEMS.length-1,i+d));render()};
$("#prev").onclick=()=>go(-1);$("#next").onclick=()=>go(1);
$("#exp").onclick=()=>{const b=new Blob([JSON.stringify(ans,null,1)],{type:"application/json"});const u=URL.createObjectURL(b);const l=document.createElement("a");l.href=u;l.download="review_labels.json";l.click()};
addEventListener("keydown",e=>{if(e.target.tagName==="INPUT")return;const m={a:"ALLOW",r:"REPLAN",b:"BLOCK",s:"ABSTAIN"}[e.key.toLowerCase()];if(m)set(m);else if(e.key==="ArrowRight")go(1);else if(e.key==="ArrowLeft")go(-1)});
render();
</script></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--real", default="data/finetune/real_cases.jsonl")
    ap.add_argument("--teacher", default="data/finetune/teacher_real.jsonl")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default="data/finetune/review.html")
    args = ap.parse_args()
    items = build_items(read(args.real), read(args.teacher), args.n, args.seed)
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    Path(args.out).write_text(PAGE.replace("__ITEMS__", data), encoding="utf-8")
    print(f"{len(items)} casos -> {args.out}  (política distinta del profesor: {sum(i['policy'] != i['teacher'] for i in items)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
