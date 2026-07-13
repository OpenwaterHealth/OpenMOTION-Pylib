# Assembles the run-1 (4-camera) HTML report with base64-embedded figures.
# Usage: build_report.py [report_assets_dir]
# After analyze_sweep.py, pass <data_dir>/analysis/report_assets.
import base64
import sys
from pathlib import Path

SP = Path(__file__).parent
ASSETS = Path(sys.argv[1]) if len(sys.argv) > 1 else SP / "report_assets"

def b64(name):
    return base64.b64encode((ASSETS / name).read_bytes()).decode()

IMGS = {k: b64(f"{k}.png") for k in [
    "warmup_left", "warmup_right", "cooling", "mean_vs_temp",
    "std_vs_temp", "timeseries_example", "tau_compare"]}

HTML = """<title>OpenMOTION camera thermal duty-cycle report</title>
<style>
:root{
  --bg:#f9f9f7; --card:#fcfcfb; --ink:#14130f; --ink2:#52514e; --muted:#898781;
  --line:#e1e0d9; --accent:#1c5cab; --accent-soft:#e8f0fb;
  --crit:#d03b3b; --crit-tint:rgba(208,59,59,.07);
  --serious:#b45f38; --good:#0a7d0a; --code:#f0efec;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0d0d0d; --card:#1a1a19; --ink:#f2f1ec; --ink2:#c3c2b7; --muted:#898781;
  --line:#2c2c2a; --accent:#5598e7; --accent-soft:#16273d;
  --crit:#e66767; --crit-tint:rgba(230,103,103,.10);
  --serious:#ec835a; --good:#0ca30c; --code:#232322;
}}
:root[data-theme="dark"]{
  --bg:#0d0d0d; --card:#1a1a19; --ink:#f2f1ec; --ink2:#c3c2b7; --muted:#898781;
  --line:#2c2c2a; --accent:#5598e7; --accent-soft:#16273d;
  --crit:#e66767; --crit-tint:rgba(230,103,103,.10);
  --serious:#ec835a; --good:#0ca30c; --code:#232322;
}
:root[data-theme="light"]{
  --bg:#f9f9f7; --card:#fcfcfb; --ink:#14130f; --ink2:#52514e; --muted:#898781;
  --line:#e1e0d9; --accent:#1c5cab; --accent-soft:#e8f0fb;
  --crit:#d03b3b; --crit-tint:rgba(208,59,59,.07);
  --serious:#b45f38; --good:#0a7d0a; --code:#f0efec;
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
h2{font-size:1.12rem;margin:2.4rem 0 .3rem;padding-bottom:.3rem;border-bottom:1px solid var(--line);letter-spacing:-.005em;}
h3{font-size:.95rem;margin:1.2rem 0 .2rem;color:var(--ink);}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:1.3rem 0;}
.tile{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.7rem .8rem;}
.tile .v{font-size:1.28rem;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.01em;}
.tile .l{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:.15rem;}
.tile .s{font-size:.76rem;color:var(--ink2);margin-top:.1rem;}
figure{margin:1.1rem 0;background:#fcfcfb;border:1px solid var(--line);border-radius:6px;
  padding:10px;overflow-x:auto;}
figure img{display:block;max-width:100%;height:auto;margin:0 auto;}
figcaption{font-size:.8rem;color:var(--ink2);padding:.5rem .3rem 0;max-width:none;}
table{border-collapse:collapse;width:100%;font-size:.82rem;margin:.8rem 0;
  font-variant-numeric:tabular-nums;}
.tblwrap{overflow-x:auto;}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  font-weight:600;text-align:right;padding:.35rem .55rem;border-bottom:1px solid var(--line);}
td{padding:.32rem .55rem;border-bottom:1px solid var(--line);text-align:right;color:var(--ink);}
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
a{color:var(--accent);}
</style>
<div class="wrap">
<header>
<div class="kicker">OpenMOTION &middot; bench characterization</div>
<h1>Camera thermal duty-cycle: warm-up, cool-down, and the temperature dependence of image statistics</h1>
<div class="meta">Overnight off-time sweep &middot; 2026-07-11, 00:02&ndash;07:44 &middot; 10 &times; 20-min scans, mask 0x0F both sensors, 40 fps &middot;
cameras-off periods 5&ndash;55 min &middot; <b>3.84 M frames, 10.7 GB raw</b> &middot; scripts/camera_scan_off_cycle.py</div>
</header>

<div class="tiles">
<div class="tile"><div class="l">Scans completed</div><div class="v">10 / 10</div><div class="s">every cycle succeeded</div></div>
<div class="tile"><div class="l">Warm-up (two-pole)</div><div class="v">14&ndash;27 s</div><div class="s">+ slow pole 5&ndash;8 min</div></div>
<div class="tile"><div class="l">Cool-down &tau;</div><div class="v">2&ndash;3.5 min</div><div class="s">floors: left 34, right 31 &deg;C</div></div>
<div class="tile"><div class="l">Mean drift</div><div class="v">&le;0.07 %/&deg;C</div><div class="s">scales with brightness</div></div>
<div class="tile"><div class="l">Mid-scan data loss</div><div class="v good">0 frames</div><div class="s">across 3.84 M</div></div>
<div class="tile"><div class="l">Anomaly</div><div class="v bad">4 scans</div><div class="s">frozen temp readout</div></div>
</div>

<h2>Key findings</h2>
<ul>
<li><b>Warm-up is two-pole.</b> Die temperature after power-on follows a fast pole of <b>&tau;&#8321; &asymp; 14&ndash;27 s</b> (die/package) plus a slow pole of <b>&tau;&#8322; &asymp; 5&ndash;8 min</b> (module/board). A single exponential leaves 1.2&ndash;2.5 &deg;C RMS error; the two-pole fit leaves 0.15&ndash;0.35 &deg;C. After 20 minutes the die sits at a per-camera plateau of <b>66&ndash;98 &deg;C</b> that reproduces within ~1 &deg;C across all ten scans.</li>
<li><b>Cool-down is faster than warm-up saturation.</b> Start temperature vs preceding off-time fits a single exponential with <b>&tau;<sub>cool</sub> &asymp; 3.5&ndash;2.6 min (left)</b> and <b>&asymp; 2 min (right)</b> to a floor of 34 / 31 &deg;C. Practically: a 5-min off period leaves the module ~7&ndash;8 &deg;C hot; <b>15 min recovers to within &lt;1 &deg;C of floor</b>; beyond 25 min there is no measurable difference. All four cameras of a module cool to the <b>same shared floor</b> (&asymp;34 &deg;C left, &asymp;31 &deg;C right): the 30 &deg;C per-camera spread while running vanishes when off &mdash; after 25 min they sit within 0.5 &deg;C of each other.</li>
<li><b>Image mean tracks die temperature linearly and weakly</b> &mdash; +0.005 to +0.42 counts/&deg;C depending on camera. In relative terms the slope is 0.004&ndash;0.07 %/&deg;C and scales with scene brightness, i.e. it behaves like a small gain/responsivity drift, not an additive dark-current offset.</li>
<li><b>Standard deviation rises with temperature, and dominates on dark channels.</b> Absolute slopes are small (&asymp;+0.02&ndash;0.08 counts/&deg;C), but on the dimmest camera std grows <b>~60 % over one 20-min scan</b> (vs ~1 % on bright cameras). Any contrast-derived quantity (std/mean) on low-signal channels inherits the thermal time constants directly.</li>
<li><b>The data is continuous.</b> Zero missing frames, zero timestamp anomalies, and zero steps in the smoothed image statistics across all 3.84 M mid-scan frames. The only discontinuities found are in the <em>temperature telemetry</em>, below.</li>
<li class="bad"><b>Temperature telemetry can silently freeze.</b> Every scan starts with 4&ndash;16 frames of stale (previous-scan) temperature, and on the right sensor the readout froze entirely for the last four scans (2.7 h) despite camera power cycles &mdash; plausible-looking but wrong values, no error raised.</li>
</ul>

<h2>1 &middot; Experiment</h2>
<p>Each cycle: power + configure cameras (mask 0x0F, both sensors) &rarr; 20-minute scan with full raw
histogram capture &rarr; cameras off &rarr; wait. The off-time swept <b>5, 15, 25, 35, 45, 55 min</b> (as specified)
plus fill-ins <b>10, 30, 40 min</b> to densify the cooling curve; power-on to scan start is a constant
7.9 &plusmn; 0.1 s, so the off duration is a clean cool-down variable. Per-frame mean and std are computed
from each 1024-bin histogram (bin index = 10-bit pixel value); die temperature comes from the frame
metadata. The lab was dark-room-ambient throughout; laser emission does not reach these cameras
(light- and dark-frame means agree to 0.01 counts), so the dataset isolates camera thermal behavior.</p>

<h2>2 &middot; Warm-up during a scan</h2>
<figure><img src="data:image/png;base64,{warmup_left}" alt="Left sensor warm-up curves"></figure>
<figure><img src="data:image/png;base64,{warmup_right}" alt="Right sensor warm-up curves">
<figcaption>Right sensor: scans after C06 are excluded (frozen temperature readout, &sect;6). Darker
blue = longer preceding off period; longer-cooled starts begin lower and converge to the same plateau.</figcaption></figure>
<p>The family of curves collapses onto a per-camera model: starting temperature moves with the
preceding off-time, the plateau does not. Cameras within a module differ by up to 30 &deg;C in
plateau &mdash; position in the array dominates (cam 0 coolest at 66&ndash;73 &deg;C, cam 3 hottest at 96&ndash;98 &deg;C).
The apparent single-exponential &tau; varies with starting temperature (166&ndash;264 s for left cam 0),
which is an artifact of mixing two poles; the two-pole constants are stable scan-to-scan.</p>
<div class="tblwrap"><table>
<thead><tr><th>Camera</th><th>Scans</th><th>Plateau T&#8320;&#8322;&#8320; (&deg;C)</th><th>&tau; fast (s)</th><th>&tau; slow (s)</th><th>1-exp &tau; (s), range</th><th>RMSE 1-exp (&deg;C)</th><th>RMSE 2-exp (&deg;C)</th></tr></thead>
<tbody>
<tr><td>L cam 0</td><td>10</td><td>66.5</td><td>13.9</td><td>465</td><td>250 (166&ndash;264)</td><td>1.27</td><td>0.18</td></tr>
<tr><td>L cam 1</td><td>10</td><td>79.1</td><td>19.4</td><td>465</td><td>173 (90&ndash;184)</td><td>1.93</td><td>0.27</td></tr>
<tr><td>L cam 2</td><td>10</td><td>92.3</td><td>25.4</td><td>397</td><td>139 (74&ndash;151)</td><td>2.37</td><td>0.24</td></tr>
<tr><td>L cam 3</td><td>10</td><td>96.0</td><td>26.3</td><td>384</td><td>139 (80&ndash;149)</td><td>2.46</td><td>0.31</td></tr>
<tr><td>R cam 0</td><td>6</td><td>72.9</td><td>20.8</td><td>279</td><td>57 (41&ndash;67)</td><td>1.38</td><td>0.20</td></tr>
<tr><td>R cam 1</td><td>6</td><td>81.1</td><td>23.8</td><td>309</td><td>65 (49&ndash;71)</td><td>1.69</td><td>0.26</td></tr>
<tr><td>R cam 2</td><td>6</td><td>87.3</td><td>27.1</td><td>328</td><td>83 (62&ndash;90)</td><td>1.95</td><td>0.27</td></tr>
<tr><td>R cam 3</td><td>6</td><td>93.5</td><td>27.1</td><td>317</td><td>99 (71&ndash;109)</td><td>2.16</td><td>0.29</td></tr>
</tbody></table></div>
<p>The right module equilibrates 1.5&ndash;2&times; faster than the left (smaller slow pole and much smaller
slow-pole amplitude) &mdash; consistent with different mounting/airflow rather than different silicon:
the fast poles are nearly identical.</p>

<h2>3 &middot; Cool-down between scans</h2>
<figure><img src="data:image/png;base64,{cooling}" alt="Start temperature vs preceding off duration with exponential fits"></figure>
<p>With end temperature and off duration both measured, each camera's next-scan start temperature
fits <code>T&#8320;(W) = T_floor + (T_end &minus; T_floor)&middot;e^(&minus;W/&tau;_cool)</code> with RMSE &le; 1.4 &deg;C
(left, 9 points) and &le; 0.75 &deg;C (right, 5 points before the telemetry freeze):</p>
<div class="tblwrap"><table>
<thead><tr><th>Camera</th><th>&tau;_cool (min)</th><th>T_floor (&deg;C)</th><th>RMSE (&deg;C)</th><th>n</th><th>Residual heat after 5 min</th><th>after 15 min</th></tr></thead>
<tbody>
<tr><td>L cam 0</td><td>3.45</td><td>33.7</td><td>1.00</td><td>9</td><td>+7.7 &deg;C</td><td>+0.4 &deg;C</td></tr>
<tr><td>L cam 1</td><td>2.79</td><td>34.2</td><td>1.30</td><td>9</td><td>+7.5 &deg;C</td><td>+0.2 &deg;C</td></tr>
<tr><td>L cam 2</td><td>2.59</td><td>34.2</td><td>1.43</td><td>9</td><td>+8.4 &deg;C</td><td>+0.2 &deg;C</td></tr>
<tr><td>L cam 3</td><td>2.62</td><td>34.1</td><td>1.39</td><td>9</td><td>+9.2 &deg;C</td><td>+0.2 &deg;C</td></tr>
<tr><td>R cam 0</td><td>2.10</td><td>30.9</td><td>0.64</td><td>5</td><td>+3.9 &deg;C</td><td>+0.03 &deg;C</td></tr>
<tr><td>R cam 1</td><td>1.91</td><td>31.1</td><td>0.59</td><td>5</td><td>+3.7 &deg;C</td><td>+0.02 &deg;C</td></tr>
<tr><td>R cam 2</td><td>1.99</td><td>31.4</td><td>0.74</td><td>5</td><td>+4.5 &deg;C</td><td>+0.03 &deg;C</td></tr>
<tr><td>R cam 3</td><td>2.21</td><td>31.2</td><td>0.75</td><td>5</td><td>+5.4 &deg;C</td><td>+0.06 &deg;C</td></tr>
</tbody></table></div>
<div class="note"><p><b>Practical rule:</b> to start a scan thermally &ldquo;cold&rdquo;, allow <b>&ge;15 min</b> cameras-off
(&lt;1 &deg;C residual). 5 min leaves ~8 &deg;C of residual heat on the left module. Nothing measurable is
gained past ~25 min. Cool-down &tau; sits between the two warm-up poles, as expected for a passive
decay of the same thermal masses.</p></div>

<h3>Where each camera lands: measured start temperature vs preceding off-time</h3>
<div class="tblwrap"><table>
<thead><tr><th>Off time</th><th>L cam0</th><th>L cam1</th><th>L cam2</th><th>L cam3</th><th>R cam0</th><th>R cam1</th><th>R cam2</th><th>R cam3</th></tr></thead>
<tbody>
<tr><td>5 min</td><td>40.5</td><td>40.8</td><td>41.9</td><td>42.5</td><td>34.7</td><td>34.7</td><td>35.9</td><td>37.7</td></tr>
<tr><td>10 min</td><td>37.0</td><td>37.6</td><td>37.6</td><td>37.9</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>15 min</td><td>36.0</td><td>36.6</td><td>37.2</td><td>36.6</td><td>32.1</td><td>32.2</td><td>32.8</td><td>32.5</td></tr>
<tr><td>25 min</td><td>34.3</td><td>34.8</td><td>34.4</td><td>34.5</td><td>30.9</td><td>31.1</td><td>31.4</td><td>31.5</td></tr>
<tr><td>30 min</td><td>33.3</td><td>33.8</td><td>34.0</td><td>34.1</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>35 min</td><td>33.3</td><td>33.9</td><td>33.4</td><td>33.3</td><td>30.2</td><td>30.5</td><td>30.9</td><td>30.9</td></tr>
<tr><td>40 min</td><td>32.8</td><td>33.1</td><td>33.5</td><td>33.5</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>
<tr><td>45 min</td><td>33.3</td><td>32.9</td><td>33.1</td><td>33.1</td><td>30.4</td><td>30.6</td><td>30.6</td><td>30.2</td></tr>
<tr><td>55 min</td><td>32.8</td><td>33.2</td><td>32.6</td><td>32.4</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>
</tbody></table></div>
<p>Values are die temperature a few seconds after power-on (measuring requires the camera on;
the true unpowered temperature is slightly lower). Right-side dashes are the frozen-telemetry
scans (&sect;6). Three observations: <b>(1)</b> after a short 5-min wait the cameras still remember
their running rank &mdash; the hottest runner (cam 3) restarts warmest &mdash; but by 25 min the ranking
is gone and all four cameras of a module agree to &lt;0.5 &deg;C. <b>(2)</b> The floors are a module
property: left &asymp;34 &deg;C, right &asymp;31 &deg;C, mirroring right&rsquo;s faster cool-down and cooler plateaus
(better thermal coupling/airflow at its position). <b>(3)</b> The 35&ndash;55-min starts creep ~1 &deg;C
below the fitted floors &mdash; those waits ran between 02:00 and 07:00 as the lab cooled overnight,
so past ~25 min the &ldquo;floor&rdquo; simply tracks the room plus a couple degrees of enclosure warmth.</p>
<figure><img src="data:image/png;base64,{tau_compare}" alt="Time constant comparison per camera"></figure>

<h2>4 &middot; Image mean and std vs temperature</h2>
<figure><img src="data:image/png;base64,{mean_vs_temp}" alt="Mean vs temperature per camera with linear fits"></figure>
<figure><img src="data:image/png;base64,{std_vs_temp}" alt="Std vs temperature per camera with linear fits"></figure>
<p>Pooled over all valid scans (2-second median filtering to remove lighting flicker), both
statistics are <b>linear in die temperature over the full 30&ndash;98 &deg;C range</b> &mdash; no thresholds, no
inflection. The mean slope scales with scene brightness (brightest cameras drift most in absolute
counts), so per-count it is a nearly uniform <b>gain-like drift of ~0.04&ndash;0.07 %/&deg;C on bright
channels</b> and less on dim ones. The visible loops around the fit lines are hysteresis from ambient
light drifting slowly overnight, not a temperature nonlinearity.</p>
<div class="tblwrap"><table>
<thead><tr><th>Camera</th><th>Mean level (counts)</th><th>d(mean)/dT (counts/&deg;C)</th><th>%/&deg;C</th><th>R&sup2;</th><th>d(std)/dT (counts/&deg;C)</th><th>R&sup2;</th></tr></thead>
<tbody>
<tr><td>L cam 0</td><td>752</td><td>+0.415</td><td>0.055</td><td>0.44</td><td>+0.076</td><td>0.34</td></tr>
<tr><td>L cam 1</td><td>288</td><td>+0.103</td><td>0.036</td><td>0.53</td><td>+0.022</td><td>0.45</td></tr>
<tr><td>L cam 2</td><td>148</td><td>+0.020</td><td>0.014</td><td>0.82</td><td>+0.031</td><td>0.80</td></tr>
<tr><td>L cam 3</td><td>130</td><td>+0.005</td><td>0.004</td><td>0.91</td><td>+0.030</td><td>0.78</td></tr>
<tr><td>R cam 0</td><td>999</td><td>+0.105</td><td>0.011</td><td>0.61</td><td>&minus;0.152</td><td>0.61</td></tr>
<tr><td>R cam 1</td><td>409</td><td>+0.279</td><td>0.068</td><td>0.77</td><td>+0.056</td><td>0.72</td></tr>
<tr><td>R cam 2</td><td>161</td><td>+0.036</td><td>0.022</td><td>0.83</td><td>+0.018</td><td>0.84</td></tr>
<tr><td>R cam 3</td><td>131</td><td>+0.005</td><td>0.004</td><td>0.88</td><td>+0.023</td><td>0.76</td></tr>
</tbody></table></div>
<p><b>Why R cam 0&rsquo;s std slope is negative (saturation, measured):</b> R cam 0 is deeply clipped &mdash;
<b>~82 % of its pixels sit hard at the 1023 rail</b> (vs 0.2&ndash;0.5 % on L cam 0 and 0 % everywhere
else). Rail pixels contribute zero spread, so its histogram is a censored distribution: a large
constant spike at 1023 plus a sub-rail tail that carries all the variance. Warming raises pixel
values, pushing <em>more</em> of the tail over the rail &mdash; the clipped fraction grows from 81.8 %
(cool, 38 &deg;C) to 83.5 % (hot, 72 &deg;C) &mdash; and with most mass already at the rail, moving tail
pixels onto it shrinks the measured spread. A two-population estimate
(&sigma;&sup2; &asymp; p(1&minus;p)&middot;D&sup2; with p the clipped fraction) reproduces the fitted
&minus;0.15 counts/&deg;C in sign and magnitude. The same censoring suppresses its apparent mean
slope (0.011 %/&deg;C vs 0.055 %/&deg;C on the comparably bright L cam 0). So the negative
coefficient is a clipping artifact, not real noise reduction &mdash; underlying noise on R cam 0
almost certainly grows with temperature like every other camera. Treat all R cam 0 statistics
as saturation-limited.</p>

<h3>Relative drift within one scan &mdash; why dark channels care</h3>
<figure><img src="data:image/png;base64,{timeseries_example}" alt="Scan C05 temperature, relative mean and std drift vs time">
<figcaption>Scan C05 (left, 35-min prior cool-down). Bottom panel: on near-dark cam 3 the std climbs
~60 % over the scan and is still rising at 20 min (it follows the slow thermal pole); cam 2 gains ~17 %;
bright cameras barely move in relative terms. The mean panel shows every camera settling ~1 % once
die temperature saturates.</figcaption></figure>
<p>Because std enters speckle contrast as std/mean, low-signal channels see the largest
temperature-driven contrast change. The drift's time dependence is exactly the thermal model's:
fast settling in the first minute, then a slow creep with &tau; &asymp; 6&ndash;8 min.</p>

<h2>5 &middot; Frame-to-frame flicker (non-thermal)</h2>
<p>Frame-to-frame mean fluctuation is 0.01&ndash;0.07 % RMS, proportional to brightness, flat in time,
and independent of temperature &mdash; consistent with ambient-light flicker beating against the 40 fps
rolling shutter. It sets the noise floor the 2-s median filtering removes before fitting; it is
not a data-quality problem.</p>

<h2>6 &middot; Discontinuities &amp; anomalies</h2>
<p>The frame stream itself is exemplary: <span class="good">zero missing frames, zero duplicate or
irregular timestamps, and zero steps &gt;2 counts in the smoothed statistics</span> across all twenty
20-minute recordings. One benign 151 ms hiccup appears per scan exactly at the 1200-s duration
boundary during teardown. All genuine discontinuities are in temperature telemetry:</p>
<div class="callout">
<span class="tag">Firmware finding &middot; stale temperature readout</span>
<p><b>Scan-start staleness (every scan):</b> the first 4&ndash;16 frames (0.1&ndash;0.4 s) of each camera report
the <em>previous scan&rsquo;s end temperature</em> &mdash; e.g. a scan starting at a true 37.6 &deg;C first reports
67.0 &deg;C, the exact end temp from 5 minutes earlier. Values refresh per camera every 4 frames
(10 Hz), staggered across cameras: the firmware serves cached readings across camera power cycles.</p>
<p><b>Persistent freeze (right sensor, scans C07&ndash;C10):</b> after the C06&rarr;C07 power cycle the right
module&rsquo;s temperature polling never resumed. All four cameras reported constants
(72.34 / 80.48 / 86.82 / 93.15 &deg;C &mdash; C06&rsquo;s end temps, <code>nunique = 1</code> in the raw CSVs) for
the final <b>2.7 hours</b>, surviving four camera power cycles. Image data remained normal; only
temperature froze. Nothing flags the failure &mdash; values look plausible downstream.</p>
<p><b>Suggested fixes:</b> invalidate the cached temperature on camera power-off; add a staleness
counter/watchdog on the temp-poll loop in sensor-fw (<code>camera_manager.c</code>); surface last-update
age in frame metadata so hosts can reject stale temps.</p>
</div>
<p>Remaining minor items: ~10 of 80 camera-scans show a single 1.0&ndash;1.8 &deg;C step during the steep
first-minute warm-up &mdash; threshold-straddling readout quantization, not data corruption. No negative
timestamps (no cross-scan frame leakage) occurred in any scan.</p>

<h2>7 &middot; Predictive models</h2>
<p>Constants from the tables above (&sect;2&ndash;&sect;4); temperatures in &deg;C, times in seconds.</p>
<div class="formula">Warm-up (two-pole, from power-on with start temp T&#8320;):
  T(t) = T&#8734; + A&middot;(T&#8320; &minus; T&#8734;)&middot;e^(&minus;t/&tau;&#8321;) + (1&minus;A)&middot;(T&#8320; &minus; T&#8734;)&middot;e^(&minus;t/&tau;&#8322;)
  &tau;&#8321; = 14&ndash;27 s, &tau;&#8322; = 280&ndash;500 s per camera; A &asymp; 0.55&ndash;0.75 (fast-pole fraction)
  quick form: T(t) &asymp; T&#8734; &minus; (T&#8734; &minus; T&#8320;)&middot;e^(&minus;t/&tau;_eff), &tau;_eff from &sect;2 (start-temp dependent)

Cool-down (off for W seconds after ending at T_end):
  T&#8320;(W) = T_floor + (T_end &minus; T_floor)&middot;e^(&minus;W/&tau;_cool)
  &tau;_cool = 125&ndash;207 s, T_floor = 31&ndash;34 &deg;C per camera (&sect;3)

Image statistics at temperature T (per camera, &sect;4):
  mean(T) = a_m + b_m&middot;T      b_m = +0.005 &hellip; +0.42 counts/&deg;C
  std(T)  = a_s + b_s&middot;T      b_s = +0.02 &hellip; +0.08 counts/&deg;C (except saturated R cam 0)

Composed prediction for a scan starting at T&#8320;:
  mean(t) = a_m + b_m&middot;T(t; T&#8320;)   &mdash; validated: mean follows T within flicker noise</div>
<p>Worst-case application: a scan started 5 minutes after a previous one runs ~8 &deg;C hotter at t=0,
which biases bright-channel mean by ~3 counts (&lt;0.5 %) and dark-channel std by ~0.25 counts
(~10&ndash;15 % of its value) until the thermal transient settles.</p>

<h2>8 &middot; Methods &amp; data</h2>
<ul>
<li><b>Data:</b> <code>scan_off_cycle_data\\</code> &mdash; raw per-frame histograms (10.7 GB), telemetry CSVs, scans.db;
derived per-frame stats and all fit tables + figures in <code>scan_off_cycle_data\\analysis\\</code>.</li>
<li><b>Reduction:</b> mean/std computed per frame from the 1024-bin histogram; series time-ordered by
timestamp (frame_id wraps at 256 = 6.4 s and must not be used for ordering).</li>
<li><b>Cleaning:</b> first 0.5 s of each scan excluded (stale temperature window); temperature despiked
with a 5-frame rolling median (found nothing beyond the stale windows); frozen-readout scans excluded
from temperature fits; 2-s rolling median on mean/std before regression (removes lighting flicker).</li>
<li><b>Fitting:</b> exponentials via 1-D/2-D log-grid search over &tau; with closed-form linear least
squares for the remaining parameters, golden-section refinement; validated on synthetic data.</li>
<li><b>Limitations:</b> die temperature is the only thermometer (no independent ambient probe); scene
is bench ambient light, so mean/std slopes fold in any responsivity-vs-temperature effect of the
scene itself; right-sensor models use 6 of 10 scans (pre-freeze); single overnight ambient drift is
visible as hysteresis loops in &sect;4 and bounds accuracy at the quoted R&sup2;.</li>
</ul>

<div class="foot">Generated 2026-07-11 from the SWEEP8H dataset (subjects SWEEP8H_C01&hellip;C10) &middot;
analysis scripts: reduce_raw.py / analyze_sweep.py / fitting.py (session scratchpad, copies of outputs in
scan_off_cycle_data\\analysis) &middot; experiment driver: scripts/camera_scan_off_cycle.py</div>
</div>
"""

out = SP / "thermal_report.html"
html = HTML
for k, v in IMGS.items():
    html = html.replace("{" + k + "}", v)
assert "base64,{" not in html, "unreplaced figure placeholder"
out.write_text(html, encoding="utf-8")
print(out, f"{out.stat().st_size/1e6:.1f} MB")
