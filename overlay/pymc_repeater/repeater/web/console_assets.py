"""Adapt the upstream compiled Console to this backend during overlay deployment.

The Console's Vue sources are not shipped in the WM1303 repository. Keep
changes limited to presentation: peer keys remain identifiers, policy editing
uses the live backend, and missing retry measurements stay unavailable. Recognize every replacement
before publishing a module so an upstream change cannot leave a partial patch.
"""

import os
from pathlib import Path
import stat
import sys
import tempfile


NEIGHBOR_REPLACEMENTS = (
    ("s(e.peer_hash)", "s(e.friendly_name||e.peer_hash)"),
    ("s(G.value.peer_hash)", "s(G.value.friendly_name||G.value.peer_hash)"),
    ("peer_hash:e.peer_hash,path_hash_size:e.path_hash_size",
     "peer_hash:e.peer_hash,friendly_name:e.friendly_name,path_hash_size:e.path_hash_size"),
    ("Peer: ${t.peer_hash}", "Peer: ${t.friendly_name||t.peer_hash}"),
    ("${t.peer_hash} ${t.path_hash_size}", "${t.friendly_name||``} ${t.peer_hash} ${t.path_hash_size}"),
    ("case`peer_hash`:return e.peer_hash;", "case`peer_hash`:return e.friendly_name||e.peer_hash;"),
    ("Search peer hash", "Search node name or peer hash"),
)

POLICY_REPLACEMENTS = (
    ("disabled:!0,onClick:$},` Edit Settings `",
     "disabled:s.value,onClick:$},` Edit Settings `"),
    (" Packet policy enforcement is unavailable in this WM1303 build. "
     "Configure radio forwarding in the Manager's Bridge tab. ",
     " Policy and object management with chained rule conditions "),
)

LBT_REPLACEMENTS = (
    ("c(Y.value?.max_attempts??0)", "c(Y.value?.has_lbt_data?Y.value.max_attempts:`N/A`)"),
    ((" No LBT transmission-path data is available for this window. This is different from zero retries. ",
      " No per-packet LBT retry data is available for this window. "
      "WM1303 bridge records do not store retry attempts. "
      "Channel LBT readings are available in the Manager's Spectrum tab. "),
     " No measured CAD/LBT TX checks are available for this window. "
     "Missing check results do not imply zero retries. "),
)

DASHBOARD_REPLACEMENTS = (
    ("h(B,{title:`Up Time`,value:c.value.uptimeFormatted,color:n.uptime,data:[],showChart:!1,class:`stat-card`},null,8,[`value`,`color`])",
     "h(B,{title:`Up Time`,value:c.value.uptimeFormatted,color:n.uptime,data:[],showChart:!1,class:`stat-card`},null,8,[`value`,`color`]),"
     "h(B,{title:`RX Duty (60s)`,value:a.stats?.rx_duty_pct==null?`N/A`:a.stats.rx_duty_pct.toFixed(3)+`%`,color:n.rx,data:[],showChart:!1,class:`stat-card`},null,8,[`value`,`color`])"),
    ("class:`grid grid-cols-2 md:grid-cols-3 lg:grid-cols-7 gap-3 lg:gap-4 mb-5 stats-cards-container`",
     "class:`grid gap-3 lg:gap-4 mb-5 stats-cards-container`,style:{gridTemplateColumns:`repeat(auto-fit,minmax(min(190px,calc((100% - 12px)/2)),1fr))`}"),
)

DASHBOARD_STYLE_REPLACEMENTS = (
    ("justify-content:space-between;align-items:baseline;margin-bottom:8px;display:flex",
     "flex-direction:column;align-items:flex-start;gap:6px;margin-bottom:8px;display:flex"),
    ("font-variant-numeric:tabular-nums;font-size:22px;font-weight:700;line-height:1",
     "font-variant-numeric:tabular-nums;font-size:22px;font-weight:700;line-height:1.15;overflow-wrap:anywhere;max-width:100%"),
    ("align-items:baseline;gap:6px;display:flex",
     "align-items:baseline;gap:6px;display:flex;flex-wrap:wrap;max-width:100%"),
)

DUTY_STORE_REPLACEMENTS = (
    ("let t=e.utilization_percent;",
     "let t=e.tx_duty_pct??(e.current_airtime_ms==null?null:e.current_airtime_ms/600);"),
)

DUTY_SIDEBAR_REPLACEMENTS = (
    ("r[6]||=T(`span`,null,`Duty Cycle`,-1)",
     "r[6]||=T(`span`,{title:`TX airtime as a percentage of the last 60 seconds`},`TX Duty (60s)`,-1)"),
    ("d(l(y).dutyCycleUtilization.toFixed(1))+`% / `+d(l(y).dutyCycleMax.toFixed(1))+`% `",
     "d(l(y).dutyCycleUtilization.toFixed(3))+`% / `+d(l(y).dutyCycleMax.toFixed(1))+`% limit `"),
    ("T(`span`,un,d(l(y).dutyCycleUtilization.toFixed(3))+`% / `+d(l(y).dutyCycleMax.toFixed(1))+`% limit `,1)]),T(`div`,dn",
     "T(`span`,un,d(l(y).dutyCycleUtilization.toFixed(3))+`% / `+d(l(y).dutyCycleMax.toFixed(1))+`% limit `,1)]),"
     "T(`div`,{class:`text-xs text-content-muted`,title:`Percentage of the configured TX allowance used in the last 60 seconds`},`TX budget used: `+d((l(y).stats?.utilization_percent??0).toFixed(1))+`%`,1),T(`div`,dn"),
)

PACKET_SCOPE_REPLACEMENTS = (
    ("_(`span`,le,a(W(c.packet.route)),1)",
     "_(`span`,le,a(W(c.packet.route)),1),"
     "_(`span`,{class:`text-xs text-content-secondary dark:text-content-muted`,style:{overflowWrap:`anywhere`}},`Scope: `+a(c.packet.scope||`unknown scope`),1)"),
)

# The built-in UI shipped with the pinned OpenHop Repeater 1.1.4 release.
# Hash-specific mappings deliberately fail closed if a different build appears.
OPENHOP_114_REPLACEMENTS = {
    'Configuration-Cnh2mH3e.js': (),  # Its native policy editor is already enabled.
    'NeighbourLinks-BB3d0leo.js': NEIGHBOR_REPLACEMENTS,
    'RfHealthCorrelation-C-IlaF7f.js': (
        ('c(X.value?.max_attempts??0)', 'c(X.value?.has_lbt_data?X.value.max_attempts:`N/A`)'),
        LBT_REPLACEMENTS[1],
    ),
    'Dashboard-CQfCREEg.js': (
        ('f(V,{title:`Up Time`,value:c.value.uptimeFormatted,color:n.uptime,data:[],showChart:!1,class:`stat-card`},null,8,[`value`,`color`])',
         'f(V,{title:`Up Time`,value:c.value.uptimeFormatted,color:n.uptime,data:[],showChart:!1,class:`stat-card`},null,8,[`value`,`color`]),'
         'f(V,{title:`RX Duty (60s)`,value:i.systemStats?.rx_duty_pct==null?`N/A`:i.systemStats.rx_duty_pct.toFixed(3)+`%`,color:n.rx,data:[],showChart:!1,class:`stat-card`},null,8,[`value`,`color`])'),
        ('class:`grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-7 gap-2 sm:gap-3 xl:gap-4 mb-5 stats-cards-container`',
         'class:`grid gap-3 lg:gap-4 mb-5 stats-cards-container`,style:{gridTemplateColumns:`repeat(auto-fit,minmax(min(190px,calc((100% - 12px)/2)),1fr))`}'),
    ),
    'Dashboard-Bkt1lYRl.css': (
        ('justify-content:space-between;align-items:flex-end;gap:8px;margin-bottom:8px;display:flex',
         'flex-direction:column;align-items:flex-start;gap:6px;margin-bottom:8px;display:flex'),
        ('font-variant-numeric:tabular-nums;font-weight:700;line-height:1}',
         'font-variant-numeric:tabular-nums;font-weight:700;line-height:1.15;overflow-wrap:anywhere;max-width:100%}'),
        ('flex-shrink:0;align-items:baseline;gap:6px;margin-left:auto;display:flex',
         'flex-shrink:0;align-items:baseline;gap:6px;margin-left:0;display:flex;flex-wrap:wrap;max-width:100%'),
    ),
    'system-DJ1E8eZb.js': (
        ('let n=e.utilization_percent;typeof n==`number`&&(g.value=n*_.value/100)',
         'let n=e.tx_duty_pct??(e.current_airtime_ms==null?null:e.current_airtime_ms/600);typeof n==`number`&&(g.value=n)'),
    ),
    'index-BaPuRw8s.js': (
        ('r[6]||=D(`span`,null,`Duty Cycle`,-1)',
         'r[6]||=D(`span`,{title:`TX airtime as a percentage of the last 60 seconds`},`TX Duty (60s)`,-1)'),
        ('p(d(h).dutyCycleUtilization.toFixed(1))+`% / `+p(d(h).dutyCycleMax.toFixed(1))+`% `',
         'p(d(h).dutyCycleUtilization.toFixed(3))+`% / `+p(d(h).dutyCycleMax.toFixed(1))+`% limit `'),
        ('D(`span`,ar,p(d(h).dutyCycleUtilization.toFixed(3))+`% / `+p(d(h).dutyCycleMax.toFixed(1))+`% limit `,1)]),D(`div`,or',
         'D(`span`,ar,p(d(h).dutyCycleUtilization.toFixed(3))+`% / `+p(d(h).dutyCycleMax.toFixed(1))+`% limit `,1)]),'
         'D(`div`,{class:`text-xs text-content-muted`},`TX budget used: `+p((d(h).stats?.utilization_percent??0).toFixed(1))+`%`,1),D(`div`,or'),
    ),
    'PacketDetailsModal-QQ4aXcuM.js': (
        ('_(`span`,le,a(U(c.packet.route)),1)',
         '_(`span`,le,a(U(c.packet.route)),1),_(`span`,{class:`text-xs text-content-secondary dark:text-content-muted`,style:{overflowWrap:`anywhere`}},`Scope: `+a(c.packet.scope||`unknown scope`),1)'),
    ),
}


def adapt_module(source, replacements):
    for before, after in replacements:
        # Some 'before' expressions occur inside the patched text (search).
        if after in source:
            continue
        candidates = (before,) if isinstance(before, str) else before
        matches = [candidate for candidate in candidates if candidate in source]
        if not matches:
            raise ValueError(f"Unrecognized Console module; missing expression: {before}")
        for candidate in matches:
            source = source.replace(candidate, after)
    return source


def patch_console_assets(html_dir):
    assets = Path(html_dir) / "assets"
    pending = []
    for pattern, replacements in (
        ("NeighbourLinks-*.js", NEIGHBOR_REPLACEMENTS),
        ("Configuration-*.js", POLICY_REPLACEMENTS),
        ("RfHealthCorrelation-*.js", LBT_REPLACEMENTS),
        ("Dashboard-*.js", DASHBOARD_REPLACEMENTS),
        ("Dashboard-*.css", DASHBOARD_STYLE_REPLACEMENTS),
        ("system-*.js", DUTY_STORE_REPLACEMENTS),
        ("index-*.js", DUTY_SIDEBAR_REPLACEMENTS),
        ("PacketDetailsModal-*.js", PACKET_SCOPE_REPLACEMENTS),
    ):
        for module in sorted(assets.glob(pattern)):
            source = module.read_text(encoding="utf-8")
            # The API client and Pinia store both ship as system-*.js.
            if pattern == 'system-*.js' and 'dutyCycleUtilization' not in source:
                continue
            updated = adapt_module(source, OPENHOP_114_REPLACEMENTS.get(module.name, replacements))
            if updated != source:
                pending.append((module, updated))
    # Validate every module first. Keep the existing ownership/mode when the
    # installer runs as root and atomically replace the complete module.
    for module, updated in pending:
        previous = module.stat()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{module.name}.", dir=module.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (previous.st_uid, previous.st_gid):
                    os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(previous.st_mode))
                stream.write(updated)
            os.replace(temporary, module)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return len(pending)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: console_assets.py <Console html directory>")
    print(f"Adapted {patch_console_assets(sys.argv[1])} Console modules")
