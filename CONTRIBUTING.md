# Contributing

**Pull requests are welcome** — fixes, new screens, translations, better
wiring advice, anything.

Этот файл на английском, потому что GitHub показывает ссылку на него всем,
кто открывает pull request. Подробное описание архитектуры — в
[ВКЛАД.md](ВКЛАД.md), на русском.

---

## Before you open a pull request

Everything here runs **without the hardware**. You need Python 3.11+ and
Pillow; numpy and scikit-learn are optional.

```bash
cd pi
py app/test_main.py          # and the five other test_*.py files, 446 checks
py tools/check_imports.py    # every import resolves to a name that exists
py tools/check_config.py     # config keys and code agree, both directions
py tools/check_layout.py     # no text off-screen, four states of data
py tools/check_layout.py --en
py tools/check_language.py   # nothing left untranslated in English
py tools/walk_menu.py        # no dead ends in the menu
py ../pc/check_strings.py    # every desktop-app string exists in every language
```

All of them must pass. They are fast — the whole set runs in under a minute
on a laptop.

To see your change, `cd pi && py preview_server.py` opens the real rendering
code in a browser at `localhost:842`, with sliders for fake sensor values.

---

## What we ask

**Explain why, not what.** The comments in this project are its main asset:
they record why a thing is the way it is, and which bug made it that way.
A comment saying `# increment the counter` above `counter += 1` is noise; a
comment saying why the counter exists is not.

**One change per pull request.** A fix and a refactor in one diff are hard
to review and impossible to revert separately.

**Add a check when you fix a bug.** Most of the checks above exist because
something broke once. If your bug could come back, it should be caught by a
run, not by someone's memory.

**Russian or English are both fine** — in code, in comments, in the pull
request itself. The project is written in Russian and nobody will ask you to
switch.

---

## Things that need doing

See [TODO.md](TODO.md) for the current state. Wanted in particular:

- a 3D-printable case — the constraints are worked out in [КОРПУС.md](КОРПУС.md)
- support for other displays and sensors
- an English translation of the documentation
- anything that makes the assembly instructions clearer for someone who has
  never soldered
