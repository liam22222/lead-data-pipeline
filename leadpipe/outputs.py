"""Deliver leads: CSV for the CRM, Slack alert for hot ones, and a one-file HTML report."""
from __future__ import annotations

import csv
import html
import json
import logging
from collections import Counter
from pathlib import Path

from leadpipe.http import HttpClient
from leadpipe.models import Lead

log = logging.getLogger(__name__)

CSV_FIELDS = ["score", "hot", "name", "website", "email", "phone", "address", "city",
              "online_booking", "premium_services", "has_contact_form", "site_platform", "is_chain",
              "ai_reason", "ai_angle", "rule_score", "rule_reasons", "socials", "site_status",
              "new_this_run", "source_id", "lat", "lon", "first_seen", "last_updated"]


def sort_leads(leads: list[Lead]) -> list[Lead]:
    return sorted(leads, key=lambda l: (-(l.score or 0), l.name.lower()))


def write_csv(leads: list[Lead], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for lead in sort_leads(leads):
            row = lead.model_dump()
            out = {}
            for k in CSV_FIELDS:
                v = row.get(k)
                out[k] = "; ".join(v) if isinstance(v, list) else (
                    json.dumps(v) if isinstance(v, dict) else v)
            writer.writerow(out)
    return path


def slack_message(leads: list[Lead], niche: str, city: str, limit: int = 10) -> dict | None:
    hot = [l for l in sort_leads(leads) if l.hot and l.new_this_run][:limit]
    if not hot:
        return None
    lines = [f"*{len(hot)} new hot {niche.lower()} in {city}*"]
    for l in hot:
        contact = l.email or l.phone or "no contact"
        why = l.ai_reason or "; ".join(r.split(" ", 1)[1] for r in l.rule_reasons[:3])
        site = f"<{l.website}|{l.name}>" if l.website else l.name
        lines.append(f"• *{l.score:g}/10* {site}: {contact}\n   _{why}_")
    return {"text": "\n".join(lines)}


def send_slack(leads: list[Lead], niche: str, city: str, webhook: str, http: HttpClient) -> bool:
    msg = slack_message(leads, niche, city)
    if not msg:
        return False
    resp = http.post(webhook, json=msg, timeout=15)
    if resp.status_code >= 300:
        log.warning("Slack webhook returned %s: %s", resp.status_code, resp.text[:200])
        return False
    return True


def stats(leads: list[Lead]) -> dict:
    return {
        "found": len(leads),
        "with_website": sum(1 for l in leads if l.website),
        "with_email": sum(1 for l in leads if l.email),
        "no_booking": sum(1 for l in leads if l.site_status == "ok" and not l.online_booking),
        "hot": sum(1 for l in leads if l.hot),
        "new": sum(1 for l in leads if l.new_this_run),
        "ai_scored": sum(1 for l in leads if l.ai_score is not None),
    }


def _e(v) -> str:
    return html.escape(str(v)) if v not in (None, "") else ""


def write_report(leads: list[Lead], path: Path, niche: str, city: str, generated: str) -> Path:
    s = stats(leads)

    def pct(n: int) -> int:
        return round(100 * n / max(len(leads), 1))
    buckets = Counter(int(min(l.score or 0, 9.99)) for l in leads)
    peak = max(buckets.values(), default=1)
    bars = "".join(
        f'<div class="bar"><div class="fill {"hotfill" if b >= 7 else ""}" style="height:{100 * buckets.get(b, 0) / peak:.0f}%"></div>'
        f'<span>{b}</span><em>{buckets.get(b, 0)}</em></div>'
        for b in range(10)
    )
    services = Counter(s_ for l in leads for s_ in l.premium_services).most_common(8)
    platforms = Counter(l.site_platform for l in leads if l.site_platform).most_common(6)

    def chips(items):
        return "".join(f"<li>{_e(k)} <b>{v}</b></li>" for k, v in items) or "<li>none found</li>"

    rows = []
    for l in sort_leads(leads):
        why = l.ai_reason or "; ".join(l.rule_reasons)
        site = f'<a href="{_e(l.website)}" target="_blank" rel="noopener">{_e(l.name)}</a>' if l.website else _e(l.name)
        rows.append(
            f'<tr class="{"hot" if l.hot else ""}"><td class="num">{l.score:g}</td><td>{site}'
            f'{" <small>NEW</small>" if l.new_this_run else ""}<div class="sub">{_e(l.address)}</div></td>'
            f'<td>{_e(l.email)}<div class="sub">{_e(l.phone)}</div></td>'
            f'<td>{"✓" if l.online_booking else ("—" if l.site_status == "ok" else "?")}</td>'
            f'<td>{_e(", ".join(l.premium_services))}</td>'
            f'<td>{_e(why)}{f"<div class=angle>💬 {_e(l.ai_angle)}</div>" if l.ai_angle else ""}</td></tr>'
        )

    kpis = [("Found", s["found"], "in OpenStreetMap"),
            ("Have a website", s["with_website"], f'{pct(s["with_website"])}%'),
            ("Direct email", s["with_email"], f'{pct(s["with_email"])}%'),
            ("No online booking", s["no_booking"], "the opportunity"),
            ("Hot leads", s["hot"], f'score ≥ threshold')]
    kpi_html = "".join(f'<div class="kpi"><span>{k}</span><b>{v}</b><small>{d}</small></div>' for k, v, d in kpis)

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(niche)} leads: {_e(city)}</title>
<style>
:root{{--bg:#f6f5f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6a65;--line:#e6e4dd;--acc:#c2410c;--ok:#15803d}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;background:var(--bg);color:var(--ink)}}
main{{max-width:1180px;margin:0 auto;padding:32px 20px 60px}}h1{{font-size:28px;margin:0}}.lede{{color:var(--mute);margin:4px 0 24px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px}}
.kpi,.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}}
.kpi span{{color:var(--mute);font-size:13px}}.kpi b{{display:block;font-size:30px;margin:2px 0}}.kpi small{{color:var(--mute)}}
.grid{{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px;margin-bottom:16px}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
h2{{font-size:15px;margin:0 0 12px}}.bars{{display:flex;gap:6px;height:140px;align-items:flex-end}}
.bar{{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;justify-content:flex-end}}
.fill{{width:100%;background:#d6d3ca;border-radius:6px 6px 0 0;min-height:2px}}.hotfill{{background:var(--acc)}}
.bar span{{font-size:12px;color:var(--mute)}}.bar em{{font-size:11px;font-style:normal}}
ul{{list-style:none;margin:0;padding:0}}li{{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--line)}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th{{text-align:left;color:var(--mute);font-weight:500;padding:8px;border-bottom:1px solid var(--line);cursor:pointer}}
td{{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}}tr.hot td:first-child{{color:var(--acc);font-weight:700}}
.num{{font-variant-numeric:tabular-nums;font-weight:600}}.sub{{color:var(--mute);font-size:12px}}a{{color:var(--ink)}}
small{{background:#fde68a;border-radius:4px;padding:0 4px;font-size:10px}}.angle{{color:var(--ok);font-size:12px;margin-top:4px}}
input{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:10px;font:inherit}}
footer{{color:var(--mute);font-size:12px;margin-top:20px}}
</style></head><body><main>
<h1>{_e(niche)} in {_e(city)}</h1>
<p class="lede">Lead report generated {_e(generated)} · sourced from OpenStreetMap, enriched from each website, scored {"by Claude against the ideal customer profile" if s["ai_scored"] else "with transparent rules"}.</p>
<div class="kpis">{kpi_html}</div>
<div class="grid">
<div class="card"><h2>Score distribution</h2><div class="bars">{bars}</div></div>
<div class="card"><h2>Premium services seen</h2><ul>{chips(services)}</ul></div>
<div class="card"><h2>Website platforms</h2><ul>{chips(platforms)}</ul></div>
</div>
<div class="card"><h2>All leads</h2><input id="q" placeholder="Filter by name, email, service...">
<table id="t"><thead><tr><th>Score</th><th>Business</th><th>Contact</th><th>Online booking</th><th>Premium services</th><th>Why</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<footer>Map data © OpenStreetMap contributors (ODbL). Generated by leadpipe.</footer>
</main><script>
const q=document.getElementById('q'),rows=[...document.querySelectorAll('#t tbody tr')];
q.addEventListener('input',()=>{{const v=q.value.toLowerCase();rows.forEach(r=>r.style.display=r.innerText.toLowerCase().includes(v)?'':'none')}});
document.querySelectorAll('#t th').forEach((th,i)=>th.addEventListener('click',()=>{{
 const tb=document.querySelector('#t tbody'),dir=th.dataset.d=th.dataset.d==='1'?'-1':'1';
 [...tb.rows].sort((a,b)=>{{const x=a.cells[i].innerText,y=b.cells[i].innerText,nx=parseFloat(x),ny=parseFloat(y);
  return (isNaN(nx)||isNaN(ny)?x.localeCompare(y):nx-ny)*dir}}).forEach(r=>tb.appendChild(r))}}));
</script></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
