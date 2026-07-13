# Assembles the 16-camera (run 2) HTML report with base64-embedded figures.
# Usage: build_report_ff.py [report_assets_dir]
# After analyze_sweep.py ff, pass <data_dir>/analysis/report_assets.
import base64
import sys
from pathlib import Path

SP = Path(__file__).parent
ASSETS = Path(sys.argv[1]) if len(sys.argv) > 1 else SP / "report_assets_ff"

def b64(name):
    return base64.b64encode((ASSETS / name).read_bytes()).decode()

IMGS = {k: b64(f"{k}.png") for k in [
    "warmup_left", "warmup_right", "cooling", "tau_compare",
    "timeseries_example"]}

HTML = """<title>OpenMOTION 16-camera thermal duty-cycle report</title>
<style>
:root{
  --bg:#f9f9f7; --card:#fcfcfb; --ink:#14130f; --ink2:#52514e; --muted:#898781;
  --line:#e1e0d9; --accent:#1c5cab; --accent-soft:#e8f0fb;
  --crit:#d03b3b; --crit-tint:rgba(208,59,59,.07);
  --good:#0a7d0a; --code:#f0efec;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0d0d0d; --card:#1a1a19; --ink:#f2f1ec; --ink2:#c3c2b7; --muted:#898781;
  --line:#2c2c2a; --accent:#5598e7; --accent-soft:#16273d;
  --crit:#e66767; --crit-tint:rgba(230,103,103,.10);
  --good:#0ca30c; --code:#232322;
}}
:root[data-theme="dark"]{
  --bg:#0d0d0d; --card:#1a1a19; --ink:#f2f1ec; --ink2:#c3c2b7; --muted:#898781;
  --line:#2c2c2a; --accent:#5598e7; --accent-soft:#16273d;
  --crit:#e66767; --crit-tint:rgba(230,103,103,.10);
  --good:#0ca30c; --code:#232322;
}
:root[data-theme="light"]{
  --bg:#f9f9f7; --card:#fcfcfb; --ink:#14130f; --ink2:#52514e; --muted:#898781;
  --line:#e1e0d9; --accent:#1c5cab; --accent-soft:#e8f0fb;
  --crit:#d03b3b; --crit-tint:rgba(208,59,59,.07);
  --good:#0a7d0a; --code:#f0efec;
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);margin:0;
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;}
.wrap{max-width:62rem;margin:0 auto;padding:2.2rem 1.4rem 4rem;}
header{border-bottom:1px solid var(--line);padding-bottom:1.1rem;margin-bottom:1.6rem;}
.kicker{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:600;}
h1{font-size:1.65rem;line-height:1.2;margin:.35rem 0 .5rem;text-wrap:balance;letter-spacing:-.01em;}
.meta{color:var(--ink2);font-size:.85rem;}
.meta b{color:var(--ink);font-weight:600;}
p{max-width:65ch;margin:.7rem 0;}
h2{font-size:1.12rem;margin:2.4rem 0 .3rem;padding-bottom:.3rem;border-bottom:1px solid var(--line);}
h3{font-size:.95rem;margin:1.2rem 0 .2rem;}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:1.3rem 0;}
.tile{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.7rem .8rem;}
.tile .v{font-size:1.28rem;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.01em;}
.tile .l{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:.15rem;}
.tile .s{font-size:.76rem;color:var(--ink2);margin-top:.1rem;}
figure{margin:1.1rem 0;background:#fcfcfb;border:1px solid var(--line);border-radius:6px;
  padding:10px;overflow-x:auto;}
figure img{display:block;max-width:100%;height:auto;margin:0 auto;}
figcaption{font-size:.8rem;color:var(--ink2);padding:.5rem .3rem 0;}
table{border-collapse:collapse;width:100%;font-size:.82rem;margin:.8rem 0;
  font-variant-numeric:tabular-nums;}
.tblwrap{overflow-x:auto;}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  font-weight:600;text-align:right;padding:.35rem .55rem;border-bottom:1px solid var(--line);}
td{padding:.32rem .55rem;border-bottom:1px solid var(--line);text-align:right;}
th:first-child,td:first-child{text-align:left;}
tbody tr:last-child td{border-bottom:none;}
.callout{border-left:3px solid var(--crit);background:var(--crit-tint);
  padding:.8rem 1rem;border-radius:0 6px 6px 0;margin:1rem 0;}
.callout .tag{display:inline-block;font-size:.68rem;font-weight:700;letter-spacing:.09em;
  text-transform:uppercase;color:var(--crit);margin-bottom:.25rem;}
.callout p{margin:.35rem 0;}
.note{border-left:3px solid var(--accent);background:var(--accent-soft);
  padding:.7rem 1rem;border-radius:0 6px 6px 0;margin:1rem 0;font-size:.88rem;}
.note p{margin:.3rem 0;}
.formula{background:var(--code);border:1px solid var(--line);border-radius:6px;
  padding:.7rem .9rem;margin:.8rem 0;font-family:ui-monospace,Consolas,monospace;
  font-size:.86rem;overflow-x:auto;white-space:pre;line-height:1.7;}
code{font-family:ui-monospace,Consolas,monospace;font-size:.85em;background:var(--code);
  padding:.08em .35em;border-radius:4px;}
ul{max-width:70ch;padding-left:1.2rem;}
li{margin:.35rem 0;}
li::marker{color:var(--accent);}
.foot{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);
  color:var(--muted);font-size:.78rem;}
.good{color:var(--good);font-weight:600;}
.bad{color:var(--crit);font-weight:600;}
.cut{color:var(--crit);font-weight:600;}
a{color:var(--accent);}
</style>
<div class="wrap">
<header>
<div class="kicker">OpenMOTION &middot; bench characterization &middot; run 2 of 2</div>
<h1>16-camera thermal duty-cycle: full-load heating, thermal cutoff, and cool-down</h1>
<div class="meta">Off-time sweep at full camera load &middot; 2026-07-11, 11:29&ndash;16:51 &middot;
10 &times; 20-min scans, mask 0xFF (8 cameras per sensor), 40 fps &middot; cameras-off waits 2&ndash;30 min &middot;
<b>7.14 M frames, 20.6 GB raw</b> &middot; companion to the
<a href="https://claude.ai/code/artifact/98bd6913-1ae6-4592-a79a-27de8a188dce">4-camera overnight report</a></div>
</header>

<div class="tiles">
<div class="tile"><div class="l">Scans completed</div><div class="v">10 / 10</div><div class="s">no cycle failures</div></div>
<div class="tile"><div class="l">Thermal cutoff</div><div class="v bad">114&ndash;116 &deg;C</div><div class="s">3 left cams stop streaming</div></div>
<div class="tile"><div class="l">Plateau shift</div><div class="v">+15&ndash;20 &deg;C</div><div class="s">vs 4-camera load</div></div>
<div class="tile"><div class="l">Cool-down &tau;</div><div class="v">1.3&ndash;2.6 min</div><div class="s">floors 33&ndash;42 &deg;C (daytime)</div></div>
<div class="tile"><div class="l">Frames lost to cutoff</div><div class="v bad">7.5 %</div><div class="s">532,880 frames, left side</div></div>
<div class="tile"><div class="l">Temp telemetry</div><div class="v good">0 freezes</div><div class="s">after pre-run MCU reset</div></div>
</div>

<h2>Key findings</h2>
<ul>
<li class="bad"><b>Three left-module cameras hit a hard thermal cutoff and stop streaming, every scan.</b>
Cam 3 stops at <b>116.3 &deg;C</b>, cam 5 at <b>115.1 &deg;C</b> (&plusmn;0.1 &deg;C across all ten scans);
cam 4 (threshold 114.4 &deg;C) joins in the three hottest-start scans. Hotter starts trip earlier
(160 s after a 2-min off vs 568 s after 30 min). Each power cycle + configure revives them fully
&mdash; 23 cutoffs, 23 recoveries, no permanent damage observed. The right module peaks at 113 &deg;C
and never cut out, leaving &lt;3 &deg;C of margin on its hottest camera.</li>
<li><b>Full load adds 15&ndash;20 &deg;C everywhere.</b> Plateaus that were 66&ndash;98 &deg;C with 4 cameras
per side are 69&ndash;116 &deg;C with 8. The hot spot sits mid-array on both modules (left cams 3&ndash;5,
right cams 2&ndash;4); edge cameras run 30&ndash;45 &deg;C cooler than the core.</li>
<li><b>Thermal coupling is directly visible:</b> when cams 3/5 cut out, their neighbors (2, 4, 6)
<em>cool 3&ndash;5 &deg;C mid-scan</em> &mdash; each camera's plateau includes several degrees of neighbor heating.</li>
<li><b>Cool-down stays fast at full load:</b> &tau; = 1.8&ndash;2.6 min (left), 1.3&ndash;1.6 min (right),
with the 2/4/8-min waits pinning the knee tightly. From 116 &deg;C, <b>10&ndash;15 min off is still a full
thermal reset</b>. Floors are 39&ndash;42 / 33&ndash;36 &deg;C &mdash; ~5 &deg;C above the overnight run, tracking
daytime ambient.</li>
<li><b>Dark-boundary stream stalls appear only at full load:</b> module-wide drops of 1&ndash;14 frames
in the ~0.4 s after a dark frame, at ~25 % of dark boundaries during scans 1&ndash;5 (&le;0.24 % of frames),
then almost none from scan 6 on. Reproduction recipe for the histo-stall debug work (PR&nbsp;#117).</li>
<li><b>Temperature telemetry held all day</b> (pre-run sensor reset pushed the 11.9-h timer wrap clear);
the scan-start stale window doubles with camera count (up to 40 frames / 1.0 s). One SDK gap surfaced:
the camera stream deaths above produced <em>no warning anywhere</em> &mdash; scans completed &ldquo;successfully&rdquo;
while silently losing up to 3 of 16 cameras.</li>
</ul>

<h2>1 &middot; Experiment</h2>
<p>Identical protocol to the overnight 4-camera run: per cycle, power + configure all 16 cameras,
scan 20 minutes with full raw capture, power off, wait. Off-times: <b>5, 10, 15, 20, 25, 30 min</b>
(the requested &le;30-min sweep) plus <b>2, 4, 8 min</b> to sample the cooling knee. Both sensor MCUs
were soft-reset before the run (clears the TIM5 wrap that froze right-side temperatures overnight;
fix pending on <code>fix/temp-telemetry-staleness</code>). Ambient: daytime lab &mdash; illumination was
not constant, which matters for &sect;6.</p>

<h2>2 &middot; Thermal cutoff at 114&ndash;116 &deg;C</h2>
<div class="callout">
<span class="tag">Headline finding &middot; hard per-camera temperature ceiling</span>
<p>At full 16-camera load, the three hottest cameras (left 3, 4, 5) reach a sharply repeatable die
temperature at which their histogram stream simply ends &mdash; no error, no garbage frames, a clean
truncation. The threshold is a per-camera constant (116.3 / 114.4 / 115.1 &deg;C, &sigma; &asymp; 0.05 &deg;C
over ten trials); the time to reach it depends on the starting temperature, exactly as the warm-up
model predicts. The next cycle's power-on + FPGA reprogram restores streaming every time. The
mechanism is consistent with an over-temperature protection or hard failure point (sensor PLL /
MIPI / CrossLink at junction limit) &mdash; worth confirming whether sensor-fw or the OV2312 has an
explicit OTP threshold near 115 &deg;C.</p>
<p><b>Nothing reported the loss.</b> ScanWorkflow, the pipeline and the DB all completed normally
&mdash; the only symptom is shorter raw files. The SDK should detect and flag per-camera stream
termination (task filed).</p>
</div>
<div class="tblwrap"><table>
<thead><tr><th>Scan</th><th>Off before (min)</th><th>cam 3 cutoff (s, &deg;C)</th><th>cam 4 cutoff (s, &deg;C)</th><th>cam 5 cutoff (s, &deg;C)</th></tr></thead>
<tbody>
<tr><td>C01</td><td>(first)</td><td>1042 &middot; 116.3</td><td>&mdash;</td><td>592 &middot; 115.2</td></tr>
<tr><td>C02</td><td>5</td><td>596 &middot; 116.3</td><td>&mdash;</td><td>284 &middot; 115.0</td></tr>
<tr><td>C03</td><td>10</td><td>721 &middot; 116.3</td><td>&mdash;</td><td>391 &middot; 115.2</td></tr>
<tr><td>C04</td><td>15</td><td>825 &middot; 116.3</td><td>&mdash;</td><td>466 &middot; 115.2</td></tr>
<tr><td>C05</td><td>20</td><td>898 &middot; 116.3</td><td>&mdash;</td><td>518 &middot; 115.1</td></tr>
<tr><td>C06</td><td>25</td><td>937 &middot; 116.3</td><td>&mdash;</td><td>549 &middot; 115.2</td></tr>
<tr><td>C07</td><td>30</td><td>941 &middot; 116.3</td><td>&mdash;</td><td>568 &middot; 115.0</td></tr>
<tr><td>C08</td><td>2</td><td>353 &middot; 116.2</td><td>726 &middot; 114.4</td><td>160 &middot; 115.0</td></tr>
<tr><td>C09</td><td>4</td><td>486 &middot; 116.2</td><td>893 &middot; 114.4</td><td>243 &middot; 115.1</td></tr>
<tr><td>C10</td><td>8</td><td>618 &middot; 116.3</td><td>1132 &middot; 114.4</td><td>337 &middot; 115.0</td></tr>
</tbody></table></div>
<p>Longer cool-downs buy survival time but never survival: even from the coldest start, cams 3 and 5
cross their ceilings by ~10 minutes. Cam 4 sits <em>at</em> its threshold: it survives 20 minutes from
cold starts (plateau &asymp;113 &deg;C) and trips only when a 2&ndash;8-min wait starts it warm. Practical
implication: with all 8 cameras of this left module active, sustained scans longer than ~3&ndash;10
minutes will lose the mid-array cameras unless dissipation or cooling changes.</p>

<h2>3 &middot; Warm-up at full load</h2>
<figure><img src="data:image/png;base64,{warmup_left}" alt="Left sensor warm-up curves, 8 cameras">
<figcaption>Left module: cams 3 and 5's curves terminate at their cutoff in every scan; cam 4 in the
three hot-start scans. Note cams 2, 4, 6 overshooting and then cooling 3&ndash;5 &deg;C as their hot
neighbors drop out &mdash; direct evidence of intra-module thermal coupling.</figcaption></figure>
<figure><img src="data:image/png;base64,{warmup_right}" alt="Right sensor warm-up curves, 8 cameras">
<figcaption>Right module: all 8 cameras complete every scan; hottest (cam 3) plateaus at ~112 &deg;C
&mdash; under 3 &deg;C below the left module's observed cutoffs.</figcaption></figure>
<div class="tblwrap"><table>
<thead><tr><th>Camera</th><th>20-min plateau (&deg;C)</th><th>&tau; fast (s)</th><th>&tau; slow (s)</th><th>2-exp RMSE (&deg;C)</th><th>4-cam run plateau (&deg;C)</th></tr></thead>
<tbody>
<tr><td>L cam 0</td><td>81.8</td><td>10.7</td><td>280</td><td>0.22</td><td>66.5</td></tr>
<tr><td>L cam 1</td><td>95.8</td><td>14.9</td><td>273</td><td>0.31</td><td>79.1</td></tr>
<tr><td>L cam 2</td><td>106.8</td><td>17.0</td><td>270</td><td>1.50</td><td>92.3</td></tr>
<tr><td>L cam 3</td><td class="cut">&ge;116.3 (cutoff)</td><td>23.8</td><td>401</td><td>0.24</td><td>96.0</td></tr>
<tr><td>L cam 4</td><td class="cut">113.0 (cutoff 114.4)</td><td>19.4</td><td>210</td><td>0.66</td><td>&mdash;</td></tr>
<tr><td>L cam 5</td><td class="cut">&ge;115.1 (cutoff)</td><td>26.3</td><td>412</td><td>0.29</td><td>&mdash;</td></tr>
<tr><td>L cam 6</td><td>112.4</td><td>15.9</td><td>173</td><td>1.51</td><td>&mdash;</td></tr>
<tr><td>L cam 7</td><td>102.5</td><td>18.2</td><td>263</td><td>0.43</td><td>&mdash;</td></tr>
<tr><td>R cam 0</td><td>84.4</td><td>23.8</td><td>399</td><td>0.24</td><td>72.9</td></tr>
<tr><td>R cam 1</td><td>94.5</td><td>27.1</td><td>350</td><td>0.31</td><td>81.1</td></tr>
<tr><td>R cam 2</td><td>103.9</td><td>31.0</td><td>397</td><td>0.37</td><td>87.3</td></tr>
<tr><td>R cam 3</td><td>111.6</td><td>31.0</td><td>450</td><td>0.36</td><td>93.5</td></tr>
<tr><td>R cam 4</td><td>97.2</td><td>26.3</td><td>423</td><td>0.33</td><td>&mdash;</td></tr>
<tr><td>R cam 5</td><td>90.9</td><td>25.4</td><td>350</td><td>0.27</td><td>&mdash;</td></tr>
<tr><td>R cam 6</td><td>77.8</td><td>20.8</td><td>361</td><td>0.23</td><td>&mdash;</td></tr>
<tr><td>R cam 7</td><td>68.7</td><td>18.2</td><td>397</td><td>0.19</td><td>&mdash;</td></tr>
</tbody></table></div>
<p>The two-pole structure carries over from the 4-camera run (fast die pole 11&ndash;31 s, slow module
pole 3&ndash;7.5 min) with the same quality of fit; only the amplitudes grew. Cutoff-censored plateaus
are marked &mdash; those cameras would run hotter still if they could. The larger RMSE on L cams 2/6 is
the neighbor-dropout dip, which no smooth two-pole model can follow.</p>

<h2>4 &middot; Cool-down from full-load temperatures</h2>
<figure><img src="data:image/png;base64,{cooling}" alt="Start temperature vs off duration, 16 cameras, with fits"></figure>
<div class="tblwrap"><table>
<thead><tr><th></th><th>cam 0</th><th>cam 1</th><th>cam 2</th><th>cam 3</th><th>cam 4</th><th>cam 5</th><th>cam 6</th><th>cam 7</th></tr></thead>
<tbody>
<tr><td>L &tau;_cool (min)</td><td>2.59</td><td>2.14</td><td>1.91</td><td>1.75</td><td>1.99</td><td>1.79</td><td>1.77</td><td>1.89</td></tr>
<tr><td>L floor (&deg;C)</td><td>38.7</td><td>39.5</td><td>40.6</td><td>41.1</td><td>40.6</td><td>41.6</td><td>41.4</td><td>40.0</td></tr>
<tr><td>R &tau;_cool (min)</td><td>1.46</td><td>1.42</td><td>1.33</td><td>1.49</td><td>1.56</td><td>1.33</td><td>1.27</td><td>1.30</td></tr>
<tr><td>R floor (&deg;C)</td><td>34.5</td><td>34.3</td><td>35.3</td><td>36.4</td><td>35.1</td><td>34.3</td><td>33.3</td><td>32.7</td></tr>
</tbody></table></div>
<p>The 2/4/8-min knee points make these &tau; values better-constrained than the overnight run's
(which sampled nothing between 0 and 5 min). Cooling is, if anything, slightly faster than at half
load &mdash; the module sheds heat proportionally to its excess over ambient. Floors are ~5 &deg;C above
the overnight values, consistent with daytime ambient (fit residuals 2&ndash;4 &deg;C reflect afternoon
ambient drift). The practical rule survives at full load: <b>&ge;10&ndash;15 min off = full thermal
reset even from 116 &deg;C</b>; a 2-min wait leaves ~20 &deg;C of residual heat.</p>
<figure><img src="data:image/png;base64,{tau_compare}" alt="Time constants per camera, warm-up vs cool-down"></figure>

<h2>5 &middot; Dark-boundary stream stalls (full-load only)</h2>
<p>Module-wide gaps of 1&ndash;14 frames (0.05&ndash;0.38 s) strike all 8 cameras of a side simultaneously,
always in the ~0.4 s window after a dark frame (every 15 s), at roughly a quarter of dark boundaries
&mdash; but almost exclusively during the first five scans:</p>
<div class="tblwrap"><table>
<thead><tr><th>Scan</th><th>C01</th><th>C02</th><th>C03</th><th>C04</th><th>C05</th><th>C06</th><th>C07</th><th>C08</th><th>C09</th><th>C10</th></tr></thead>
<tbody>
<tr><td>Stall events</td><td>18</td><td>20</td><td>20</td><td>20</td><td>17</td><td>2</td><td>0</td><td>2</td><td>3</td><td>1</td></tr>
<tr><td>Frames lost (per cam)</td><td>81</td><td>113</td><td>62</td><td>80</td><td>64</td><td>12</td><td>0</td><td>6</td><td>10</td><td>6</td></tr>
</tbody></table></div>
<p>Worst case &le;0.24 % of frames. The 4-camera overnight run had zero such events across 3.84 M
frames, so the trigger is the doubled per-module data rate plus dark-frame housekeeping (the
per-frame PDC dark-reference capture is recent work in exactly this window). This is very likely
the stall the <code>histo-stall-debug-flag</code> work (PR&nbsp;#117) chases &mdash; it now has a recipe:
mask 0xFF + dark boundary. Why it faded after scan 5 is unexplained (not correlated with cooldown
length, start temperature, or host-side activity); the debug flag should say.</p>

<h2>6 &middot; Image statistics</h2>
<p>Within-scan behavior reproduces the overnight findings: mean and std drift with die temperature
along the same per-camera slopes, and the relative std growth remains largest on dark channels.
The example below also shows what the thermal cutoffs look like in the data stream.</p>
<figure><img src="data:image/png;base64,{timeseries_example}" alt="Scan C05 left: temperature, relative mean and std drift, with cutoffs visible">
<figcaption>Scan C05 (left, 20-min prior cool-down): cam 5's traces end at 518 s and cam 3's at
898 s (thermal cutoff); the surviving cameras' statistics remain smooth throughout.</figcaption></figure>
<p><b>Do not use this run for pooled radiometric models.</b> Daytime lab illumination changed between
and during scans (people, daylight): pooled mean-vs-T R&sup2; collapses to 0.05&ndash;0.75 and the
apparent slopes are dominated by scene changes, not temperature (L cam 0's apparent slope inflates
7&times; with R&sup2; = 0.21). The overnight 4-camera report's models
(&asymp;0.004&ndash;0.07 %/&deg;C, gain-like) remain the reference; nothing in this run contradicts them.
Four channels run bright on this rig (L0 770, L7 721, R0 997, R7 985 counts) and R0/R7 are
saturation-limited as before.</p>

<h2>7 &middot; Telemetry &amp; data integrity</h2>
<ul>
<li><span class="good">No temperature freezes</span> in 10 scans &mdash; the pre-run MCU reset kept the
11.9-h TIM5 wrap outside the run window. The firmware fix remains the durable solution.</li>
<li>Scan-start stale-temperature window scales with camera count: 4&ndash;40 frames (0.1&ndash;1.0 s),
refresh cadence ~every 5th frame per camera, staggered across the 8 cameras. Analysis skips it
per-camera using the measured window.</li>
<li class="bad">Per-camera stream truncation is silent: 23 camera-scan streams ended early (&sect;2)
with no warning from firmware, SDK, or pipeline diagnostics. SDK task filed: flag any active camera
whose stream ends &gt;N s before the duration gate.</li>
<li>Other checks clean: zero duplicate/irregular timestamps outside the stall windows, zero
negative-timestamp leakage, the usual benign 151 ms teardown gap, and temperature readout steps
&le;2.1 &deg;C during the steepest warm-up (poll-cadence quantization, not glitches).</li>
</ul>

<h2>8 &middot; Predictive constants (full-load)</h2>
<div class="formula">Same model forms as the 4-camera report; constants above (&sect;3&ndash;&sect;4):
  T(t)  = T&#8734; + A&middot;(T&#8320;&minus;T&#8734;)&middot;e^(&minus;t/&tau;&#8321;) + (1&minus;A)&middot;(T&#8320;&minus;T&#8734;)&middot;e^(&minus;t/&tau;&#8322;)   valid until T reaches T_cut
  T&#8320;(W) = T_floor + (T_end &minus; T_floor)&middot;e^(&minus;W/&tau;_cool)
  T_cut = 116.3 / 114.4 / 115.1 &deg;C  (L cams 3/4/5; others did not reach a ceiling)
  Time-to-cutoff from the model: t_cut = &minus;&tau;_eff &middot; ln((T&#8734; &minus; T_cut)/(T&#8734; &minus; T&#8320;))  &mdash; matches the &sect;2 table.</div>

<h2>9 &middot; Methods &amp; data</h2>
<ul>
<li><b>Data:</b> <code>scan_off_cycle_data_ff\\</code> (raw 20.6 GB, telemetry, scans.db); per-frame stats,
fit tables, dropout table and figures in <code>scan_off_cycle_data_ff\\analysis\\</code>.</li>
<li><b>Pipeline:</b> identical to run 1 (timestamp-ordered, despiked, per-camera stale-window skip,
2-s median smoothing), generalized to 8 cameras/side. Truncated streams are used up to their last
frame; censored plateaus marked.</li>
<li><b>Caveats:</b> daytime ambient (scene not constant &mdash; radiometric fits unreliable, &sect;6;
cool-down floors elevated ~5 &deg;C vs overnight); left cams 3/4/5 plateau values are cutoff-censored;
right-module models use all 10 scans.</li>
</ul>

<div class="foot">Generated 2026-07-11 from the FFSWEEP dataset (subjects FFSWEEP_C01&hellip;C10) &middot;
experiment driver scripts/camera_scan_off_cycle.py &middot; companion report (4-camera overnight):
<a href="https://claude.ai/code/artifact/98bd6913-1ae6-4592-a79a-27de8a188dce">camera thermal duty-cycle report</a></div>
</div>
"""

out = SP / "thermal_report_ff.html"
html = HTML
for k, v in IMGS.items():
    html = html.replace("{" + k + "}", v)
assert "base64,{" not in html, "unreplaced figure placeholder"
out.write_text(html, encoding="utf-8")
print(out, f"{out.stat().st_size/1e6:.1f} MB")
