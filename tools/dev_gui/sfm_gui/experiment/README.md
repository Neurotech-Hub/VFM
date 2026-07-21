# Writing Custom Experiment Templates (Python API)

This is the guide for authoring your own experiments for the SFM dev GUI. An
**experiment** automates the pellet-dispensing nodes over CAN: it decides *when*
to dispense, *which* node(s), and *how* to react to what the nodes and the BNC
sync inputs do.

You write experiments in Python. There is **no base class to inherit** — a
template is just a module with a `build(...)` factory that returns a configured
`Experiment` object. A small JSON file describes the tunable parameters so the
GUI can render a form for it.

Everything an experiment does flows through the same CAN bus the manual controls
use, so **the node tiles always show live status** while an experiment runs — you
don't have to do anything special to keep them in sync.

---

## 1. Anatomy of a template

Two files per template:

```
sfm_gui/experiment/templates/<name>.py   # behavior  (the build() factory)
experiments/<name>.json                   # parameters (drives the GUI form)
```

The Python module exposes one function:

```python
from ..runner import Experiment

def build(nodes=None, *, name="<name>", **params) -> Experiment:
    node_list = list(nodes) if nodes else [1, 2, 3]
    exp = Experiment(nodes=node_list, name=name)

    @exp.on_start
    def _start(ctx):
        for n in ctx.nodes:
            ctx.dispense(n)

    exp.end_after(minutes=30)
    return exp
```

- `nodes` is injected by the GUI from the **node multi-select** in the Experiment
  panel (or defaults inside the template). Templates iterate `ctx.nodes` — never a
  hard-coded list — so node selection "just works".
- Register behavior with the `@exp.on_*` decorators (below).
- Call `exp.end_after(...)` for automatic stop conditions.
- Return the `Experiment`.

### Registering the template

`resolve_builder()` in [`schema.py`](schema.py) maps a template name to its
`build`. Add your template to the builtins dict there:

```python
from .templates import my_template as my_template_build
builtins = {
    "free_feeding": free_feeding_build,
    "my_template": my_template_build,
}
```

and re-export it in [`templates/__init__.py`](templates/__init__.py):

```python
from .my_template import build as my_template
```

(A template not in the builtins dict is still found by dynamic import
`sfm_gui.experiment.templates.<name>.build`, but adding it explicitly is clearer.)

### The JSON parameter file

```json
{
  "name": "my_template",
  "label": "My Template",
  "template": "my_template",
  "description": "One-line summary shown in the GUI.",
  "parameters": [
    { "key": "interval_s", "label": "Interval (s)", "type": "float",
      "default": 10.0, "min": 0, "max": 3600, "help": "..." }
  ]
}
```

- `template` must match the key you registered.
- Each parameter's `key` becomes a keyword argument to `build(...)`.
- Convention: `max_pellets` and duration-in-`minutes` of `0` mean "no limit".

Every parameter value passes through `coerce_param_value` before reaching
`build(...)`, so you receive it already typed. **The GUI form is generated
entirely from this JSON — you never write per-parameter UI code.**

### Parameter types

| `type` | Widget | Value passed to `build(...)` |
|--------|--------|------------------------------|
| `int` / `float` | number input (honors `min`/`max`) | `int` / `float` |
| `bool` | checkbox | `bool` |
| `str` | text input | `str` |
| `choice` | dropdown (needs `"options": [...]`) | `str` (one of the options) |
| `nodes` | one checkbox per node + **All/None** | *(drives the `nodes=` argument, below)* |
| `node_number` | one number input per node (honors `min`/`max`) | `dict {node_id: number}` — `0` = node inactive |
| `node_choice` | one dropdown per node (needs `"options"`) | `dict {node_id: str}` — **first option = inactive** |

The three node types render **one control per node** automatically — that is how
`fixed_and_random` (a role dropdown per node) and `probability_delivery` (a %
per node) are built, with **no template-specific GUI code**.

**Active nodes / the `nodes=` argument.** `build(nodes=...)` always receives the
active node set, derived in this order: a `nodes`-type param's checked list →
else a `node_number` param's keys with value > 0 → else a `node_choice` param's
keys whose value ≠ the first (inactive) option → else all nodes. So per-node
assignment *is* the active/inactive selection — a separate "active nodes" row is
only needed for templates that have no per-node param (like `free_feeding`,
which declares a `nodes` param).

### Conditional display — `visible_when`

Any param may declare `"visible_when": {"<other_key>": <value | [values]>}`; it
is shown (and collected) only when the controlling param currently equals one of
the values. Hidden params fall back to their JSON `default`. Example — show the
timer field only for the timer trigger, the channel only for BNC:

```json
{ "key": "trigger", "type": "choice", "options": ["timer", "bnc"], "default": "timer" },
{ "key": "interval_s", "type": "float", "default": 10.0,
  "visible_when": {"trigger": "timer"} },
{ "key": "bnc_channel", "type": "int", "default": 0, "min": 0, "max": 1,
  "visible_when": {"trigger": "bnc"} }
```

A per-node role dropdown looks like:

```json
{ "key": "node_roles", "label": "Node role", "type": "node_choice",
  "options": ["off", "fixed", "random"], "default": "random" }
```

Your `build(node_roles=...)` then receives `{1: "fixed", 2: "off", 3: "random"}`.
While running, the whole form (template selector + every param) is **locked**;
it re-enables on Stop or when the run ends on its own.

BNC channels are **0-based**: `0` = first BNC input (IN1), `1` = second (IN2).

---

## 2. Two styles: loop-based vs event-based

Both use the **same** callback API — the difference is only what drives the
actions.

### Loop-based (timer-driven)

Cycles are paced by timers on the runner clock. `free_feeding` is the canonical
example: dispense on start, then re-dispense a node some delay after its dome
closes.

```python
@exp.on_start
def _start(ctx):
    for n in ctx.nodes:
        ctx.dispense(n)

@exp.on_dome_closed
def _reload(ctx, ev):
    # Node-scoped timer: cancelled automatically if this node faults.
    ctx.after(2.0, lambda: ctx.dispense(ev.node_id), node=ev.node_id)
```

### Event-based (reacts to events)

Actions fire off events — a BNC pulse, a pellet loaded, a dome closing, etc.

```python
@exp.on_bnc_in
def _on_pulse(ctx, ev):
    if ev.data.get("edge") == "rising":
        ctx.dispense(ctx.nodes[0])   # dispense on every BNC rising edge
```

You can freely mix both in one template (e.g. a timer heartbeat plus BNC
overrides).

---

## 3. The `ctx` object (`ExperimentContext`)

`ctx` is passed as the first argument to every callback. It is your entire action
surface — see [`context.py`](context.py).

### Actions

| Call | Effect |
|------|--------|
| `ctx.dispense(node)` | Dispense on one node. **No-op while that node is halted by a fault** (logged). |
| `ctx.recover(node)` | Send Recover to one node (stop motion + clear its fault). |
| `ctx.broadcast_dispense()` / `ctx.broadcast_recover()` | Same, to all nodes. |
| `ctx.bnc_pulse(duration_us=100)` | Pulse the BNC OUT line. |
| `ctx.set_heartbeat_interval(node, ms)` | Reconfigure a node's heartbeat rate. |

### Per-node fault handling (sticky)

| Call | Effect |
|------|--------|
| `ctx.halt_node(node_id)` | Latch a node: cancel its timers, make its `dispense` a no-op. (The engine calls this automatically on a FAULT event — you rarely call it yourself.) |
| `ctx.recover_node(node_id)` | Clear the halt and send Recover (clears the firmware fault). |
| `ctx.is_halted(node_id)` → `bool` | Is this node latched? |
| `ctx.halted_nodes` | Sorted list of halted node IDs. |

### Timers (runner clock, not wall clock)

| Call | Effect |
|------|--------|
| `ctx.after(seconds, cb, node=0)` | One-shot. Pass `node=` to tie it to a node so it is cancelled if that node faults. |
| `ctx.every(seconds, cb, node=0)` | Repeating. |
| `ctx.cancel_timer(timer)` / `ctx.cancel_node_timers(node_id)` / `ctx.cancel_all_timers()` | Cancel. |

### Counters, time, logging, stop

| Call | Effect |
|------|--------|
| `ctx.counter(name)` / `ctx.incr(name, amount=1)` / `ctx.set_counter(name, v)` | Named integer counters. |
| `ctx.elapsed()` | Seconds since the session became active. |
| `ctx.log(name, node=0, **fields)` | Write an experiment log row (to the GUI log + CSV). |
| `ctx.stop(reason="...")` | End the whole session as soon as possible (cancels all timers). |
| `ctx.nodes` | The node IDs this session runs on. |

The engine auto-increments the `"pellets"` counter on every **`PELLET_PRESENTED`**
event (a fully delivered pellet). For a per-node tally, keep your own counter:

```python
@exp.on_pellet_presented
def _p(ctx, ev):
    ctx.incr(f"pellets_{ev.node_id}")
```

---

## 4. Events you can handle

Register with `@exp.on(EventKind.X)` or the sugar decorators. Handlers receive
`(ctx, ev)` where `ev` is a `NodeEvent(kind, node_id, timestamp, data)`.

| Sugar decorator | EventKind | `ev.data` notes |
|-----------------|-----------|-----------------|
| `@exp.on_start` / `@exp.on_end` | `SESSION_START` / `SESSION_END` | `(ctx)` only — no `ev`. |
| `@exp.on_pellet_loaded` | `PELLET_LOADED` | `pellet_count` |
| `@exp.on_pellet_presented` | `PELLET_PRESENTED` | `pellet_count` |
| `@exp.on_catch_attempt` | `CATCH_ATTEMPT` | |
| `@exp.on_dome_opened` / `@exp.on_dome_closed` | `DOME_OPENED` / `DOME_CLOSED` | derived from PG3 |
| `@exp.on_fault` | `FAULT` | `fault_code` (Timeout / Jam) |
| `@exp.on_recover` | `NODE_RECOVERED` | fired when an operator recovers a node |
| `@exp.on_bnc_in` | `BNC_IN` | `channel` (0/1), `edge` ("rising"/"falling"), `high` |
| `@exp.on_presence_changed` | `PRESENCE_CHANGED` | |

Other kinds available via `@exp.on(...)`: `LOWERING`, `LOADING`, `RAISING`,
`DOME_OPEN_WARNING`, `PG_CHANGED`, `HEARTBEAT`, `NODE_ONLINE`, `NODE_OFFLINE`.
See [`events.py`](events.py) for the full list.

### Start / end conditions

```python
exp.start_when(lambda ctx: ctx.counter("armed") > 0)   # defer SESSION_START
exp.end_when(lambda ctx: ctx.elapsed() > 600)          # custom end
exp.end_after(hours=0, minutes=30, seconds=0, pellets=100)   # duration and/or cap
```

---

## 5. Faults are sticky, per node

When a node reports a **jam or timeout**:

1. The firmware halts that node's motors and refuses further dispenses until a
   Recover — it is already sticky at the hardware level.
2. The engine calls `ctx.halt_node(node_id)`: cancels that node's timers and makes
   its `ctx.dispense(...)` a no-op. **The other nodes keep running.**
3. The node stays latched until an operator presses **Recover** on its tile (or a
   BNC IN action / `runner.recover_node`). Recovery sends Recover (clears the fault)
   and fires your `@exp.on_recover` handler so you can re-arm the node.

This means your template usually needs **no fault bookkeeping** — keep calling
`ctx.dispense(n)` for every node and halted ones simply stop receiving pellets.
Use `@exp.on_recover` to resume a node's cycle:

```python
@exp.on_fault
def _fault(ctx, ev):
    ctx.log("fault", node=ev.node_id, fault_code=ev.data.get("fault_code"))

@exp.on_recover
def _recovered(ctx, ev):
    ctx.dispense(ev.node_id)   # resume this node
```

---

## 6. BNC sync I/O

BNC is a base-station feature (Raspberry Pi GPIO); nodes are not involved.

**BNC IN** — the GUI dispatches an action per **edge** (rising / falling), each
independently optional. Configure it in the BNC panel (e.g. rising →
`start_experiment`, falling → `stop_experiment`). Inside a running experiment you
also get every edge as a `BNC_IN` event, so a template can react directly:

```python
@exp.on_bnc_in
def _edge(ctx, ev):
    if ev.data["edge"] == "rising" and ev.data["channel"] == 0:
        ctx.dispense(ctx.nodes[0])
```

**BNC OUT** — set the BNC OUT **Trigger** in the GUI to a CAN event name (e.g.
`PelletPresented`) and it pulses whenever that event arrives from any node, during
manual use *and* during an experiment. To pulse from template code directly, call
`ctx.bnc_pulse(width_us)` (e.g. on each dispense).

---

## 7. Node selection

The Experiment panel has per-node checkboxes plus **All / None**. The checked set
is passed to `build(nodes=...)`, so `ctx.nodes` is exactly the subset the operator
chose. Always iterate `ctx.nodes`; never assume `[1, 2, 3]`.

---

## 8. Worked examples in this repo

- **[`templates/free_feeding.py`](templates/free_feeding.py)** — loop-based:
  continuous reload after each dome close (default `reload_delay_s=30`, whole
  seconds); per-node sticky fault + recover.
- **[`templates/fixed_and_random.py`](templates/fixed_and_random.py)** — each
  node has a **role** (`off` / `fixed` / `random`) from a `node_choice` param.
  `fixed` nodes dispense every cycle, `random` nodes dispense with `random_prob`,
  `off` nodes are inactive. `build(node_roles={1:"fixed", 2:"off", ...})`. Trigger
  by timer or BNC rising edge. (A legacy `fixed_nodes` string is still accepted
  for headless runs.)
- **[`templates/probability_delivery.py`](templates/probability_delivery.py)** —
  each cycle delivers on **one** node, chosen by an independent **weighted
  random draw** (`random.choices`) — e.g. weights `20,80` mean node 1 has a 20%
  chance and node 2 an 80% chance *on every single cycle*; it is not a fixed
  20-of-100 allocation or round-robin, and over many cycles the observed split
  converges to the configured weights. `0` means that node is never picked.
  A `node_number` param supplies the per-node weights as `{node_id: pct}`
  (a comma-separated string is accepted for headless runs).

Both new templates accept `seed=` for reproducible runs (used by the tests).
Because both use the declarative node param types above, **neither needs any
per-template GUI code** — the form (per-node dropdowns / % inputs, plus the
trigger-gated timer/channel fields) is generated entirely from their JSON.

---

## 9. Testing your template

Run headless against synthetic events — no CAN hardware needed:

```python
from sfm_gui.experiment import EventKind, NodeEvent
from sfm_gui.experiment.templates.my_template import build

exp = build(nodes=[1, 2], interval_s=5.0, seconds=60)
runner = exp.make_runner()          # no CAN/IO bound → dry-run
runner.start(now=0.0)
runner.step(now=5.0)                 # advance the clock; timers fire

# Inject events:
runner.inject(NodeEvent(EventKind.FAULT, node_id=1, timestamp=6.0,
                        data={"fault_code": 1}))

# Inspect what was sent:
print(runner.ctx.commands_sent)      # list of (node_id, CanCmd, payload)
```

`runner.ctx.commands_sent` records every command in dry-run mode, and
`runner.ctx.is_halted(...)`, `runner.ctx.counter(...)`, and the log entries let you
assert behavior. See [`../../tests/test_experiment.py`](../../tests/test_experiment.py)
for patterns.

For a live run against a SocketCAN interface: `exp.run(interface="vcan0")`.
