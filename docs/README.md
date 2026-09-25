# Assets

| File | What it is |
|---|---|
| `hero.gif` | The animated banner used at the top of the main README. |
| `hero-still.png` | A single frame, for places that will not animate a GIF. |
| `reactor.mp4` | The source animation the banner is composited from. |
| `make_banner.py` | Regenerates a banner from scratch in pure Python — no AI, no network. Run `python docs/make_banner.py`. Needs Pillow. |

The reactor motion was generated with an image model; the lettering is drawn
afterwards in a real monospace face, because image models cannot be trusted
with text. `make_banner.py` is the dependency-free fallback if you want to
change the wording or colours without re-generating anything.
