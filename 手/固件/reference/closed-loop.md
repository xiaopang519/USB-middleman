# The closed loop — eyes → brain → hand

Everything above this file is *infrastructure*. This is the shape of the actual
automation, and it is the same shape for a game and for an office app:

```
        ┌──────────────────────────────────────────────┐
        │                                              │
   screenshot ──► detect target ──► compute error ──► move/click
        ▲              (eyes)            (brain)         (hand)
        │                                              │
        └──────────── verify the effect ◄──────────────┘
```

The only parts that change per task are **what you detect** and **what you do**.
The loop, the controller, the safety, and the verification are reusable.

`scripts/loop_demo.py` is a working, self-contained example: it spawns its own
target (a red disc at a random spot), finds it from pixels, drives the board onto
it, clicks, and scores itself with the target app's own hit counter.

Measured on this hardware:

```
target window: 1920x1080, client origin (0,0)
vision error (found vs true centre): median 0.0 px, worst 0.0 px
clicks landed on the target: 12/12
  per trial: 4-8 controller steps, ~400 ms, final error 2.2-4.0 px
```

---

## 1. Calibrate once, then think in pixels

The board works in **mouse counts**. Windows multiplies those by the
pointer-speed setting, so counts ≠ pixels.

```python
scale = m.calibrate()          # e.g. 2.580 px per count
m.inject_mouse_px(dx, dy)      # or divide yourself
```

For 1:1, set the pointer-speed slider to the middle (6/11). Otherwise expect a
constant gain — it is *not* acceleration, so it is trivially compensated.

## 2. A proportional controller is enough

The mapping is linear and exact, so a P-controller converges in a handful of
steps. Go most of the way each time and let the iteration clean up the
quantisation:

```python
for _ in range(max_steps):
    cx, cy = cursor()
    dx, dy = tx - cx, ty - cy
    if (dx*dx + dy*dy) ** 0.5 <= tolerance:      # ~4 px is plenty
        break
    m.inject_mouse(dx=round(dx / scale), dy=round(dy / scale))
    time.sleep(0.02)
```

Full step (not 0.85) matters: with a partial step you need ~11 iterations where
a full step needs 5–8, and there is no overshoot risk because the system has no
inertia beyond one report.

**Always re-read the cursor between steps.** Open-loop counting drifts.

## 3. What the detector looks like per domain

| domain | detect with | why |
|---|---|---|
| game mob / NPC / item | YOLO on the frame | colour is unreliable with overlapping sprites |
| game UI button, dialog | template match or OCR anchors | fixed chrome, high contrast |
| in-game cursor | template / brightness peak | the game draws its own; the Windows cursor is irrelevant |
| office form field | OCR + label anchoring | layouts shift; label text is stable |
| office button / tab | template or OCR | — |
| anything with a stable API | **don't** — call the API | 10× faster and far more reliable |

That last row is the important one. Reach for the board only when there is no
API and software automation is blocked or fingerprinted.

## 4. Verify every action — this is what makes it robust

Never assume the click landed. After each action, re-read the screen and confirm
something changed in the way you expected. If it did not, retry once, then give
up loudly rather than spraying clicks.

In `loop_demo.py` the verification is the target app's own hit counter. In a real
target it is a small, specific assertion: *the HP bar changed*, *the field now
contains my text*, *the row count went up*.

## 5. Latency budget

| stage | cost |
|---|---|
| `INJECT` command round trip | ~1–3 ms |
| `GetCursorPos` | < 1 ms |
| screenshot (`ImageGrab`) | 10–30 ms |
| OCR / YOLO inference | 30–100 ms |
| **closed loop** | **5–20 Hz** |

The board is never the bottleneck. **The screen capture plus inference is.**
That is fine for grinding, pathing, looting, form filling and report running.
It is not enough for anything that needs reaction time.

## 6. Safety, and why this design is unusually good at it

The board sits **in front of** the PC, so it sees the human's input as well as
its own. That gives a free, very strong kill switch:

> **If the human touches the mouse or keyboard, stop.**

Enable the monitor stream during automation; any event with a non-zero value and
a hub port that is not our own injection means a person took over. Compare
against `read_events()` while idling to learn what "quiet" looks like.

Two more that cost little:

* **Watchdog.** If the controller fails to converge in N steps, stop and report —
  never keep clicking.
* **Try/finally.** Release every button you pressed. A stuck left button is
  worse than a missed click.

And the standing rule: **never run a real game test without explicit human
approval.** `loop_demo.py` exists so you can develop and prove the loop without
touching anything real.

## 7. Two traps that silently cap accuracy

* **Window frame offset.** A normal window's *client* area begins below the title
  bar (~31 px) and inside the border (~8 px). A target drawn relative to the
  window geometry therefore sits ~32 px from where your maths thinks it is.
  `loop_demo.py` uses a **borderless fullscreen** window so client origin ==
  screen origin. For a normal window use `canvas.winfo_rootx()/rooty()`.
* **Unmapped widgets report 0.** `winfo_rootx()` returns **0** until the window
  is actually mapped, which silently turns every coordinate into garbage. Call
  `root.update()` first and print the origin so you can see it.

Both showed up here as a "vision error" of 32 px and then 943 px, while the
detector itself was pixel-exact the whole time. **When a measurement looks
imprecise, first suspect the coordinates, not the algorithm.**
