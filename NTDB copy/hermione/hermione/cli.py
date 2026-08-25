"""Command line. One surface of several -- a daemon should have a socket, not
an interface. MCP server and FUSE mount are the planned siblings.

GENERATION 1   init, add, note, find, status, consolidate, dream, forget,
               rebuild, relearn, eval
ITERATION 1b   + vault, unreachable; --vault on add
ITERATION 1c   + history
ITERATION 1d   + ask
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .ask import ask as ask_bag          # [1d]
from .config import Config
from .runtime import Bag

DEFAULT_BAG = Path(os.environ.get("HERMIONE_BAG", "~/.hermione")).expanduser()

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
OFF = "\033[0m"


def _c(s: str, code: str) -> str:
    return f"{code}{s}{OFF}" if sys.stdout.isatty() else s


def cmd_init(args) -> int:
    bag = Bag(args.bag, Config())
    print(f"bag ready at {_c(str(bag.root), BOLD)}")
    bag.close()
    return 0


def cmd_add(args) -> int:
    bag = Bag(args.bag)
    total = {}
    for target in args.paths:
        p = Path(target).expanduser()
        if not p.exists():
            print(f"  {_c('skip', YELLOW)} {p} (not found)")
            continue
        counts = bag.add(p, vault_id=args.vault or "")     # [1b]
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
        print(f"  {_c('added', GREEN)} {p}: " +
              ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if counts.get("revisions"):                        # [1a]
            print(_c(f"    {counts['revisions']} revision(s); "
                     f"{counts.get('inherited', 0)} introns kept what they "
                     f"had already learned", DIM))
    if not total:
        print("nothing ingested")
    bag.close()
    return 0


# --- iteration 1d --------------------------------------------------------
def cmd_ask(args) -> int:
    bag = Bag(args.bag)
    answer = ask_bag(bag, " ".join(args.question))
    print(answer.text)
    if args.verbose:
        print(_c(f"\n  [intent: {answer.intent}]", DIM))
    bag.close()
    return 0


# --- iteration 1b --------------------------------------------------------
def cmd_vault(args) -> int:
    bag = Bag(args.bag)
    if args.detach:
        vaults = bag.detach_vault(args.detach)
    elif args.name and args.path:
        vaults = bag.attach_vault(args.name, args.path)
        print(f"{_c('attached', GREEN)} {args.name} -> {args.path}")
    else:
        vaults = bag.ledger.vaults
    if not vaults:
        print("no vaults attached")
    for vid, p in sorted(vaults.items()):
        ok = Path(p).exists()
        mark = _c("online", GREEN) if ok else _c("offline", YELLOW)
        print(f"  {vid:16} {mark}  {p}")
    bag.close()
    return 0


def cmd_unreachable(args) -> int:
    bag = Bag(args.bag)
    missing = bag.unreachable()
    if not missing:
        print(_c("everything is reachable", GREEN))
    for m in missing[:args.limit]:
        print(f"  {_c(m['name'], BOLD)}  {_c(m['where'], DIM)}")
        if m.get("hint"):
            print(_c(f"    {m['hint']}", DIM))
    if len(missing) > args.limit:
        print(_c(f"  ... and {len(missing) - args.limit} more", DIM))
    bag.close()
    return 0


# --- iteration 1c --------------------------------------------------------
def cmd_history(args) -> int:
    bag = Bag(args.bag)
    print(json.dumps(bag.corpus.summarise(args.window), indent=2))
    bag.close()
    return 0


def cmd_note(args) -> int:
    bag = Bag(args.bag)
    text = " ".join(args.text) if args.text else sys.stdin.read()
    print(json.dumps(bag.add_note(text, name=args.name)))
    bag.close()
    return 0


def cmd_find(args) -> int:
    bag = Bag(args.bag)
    query = " ".join(args.query)
    results, outcome = bag.find(query, limit=args.limit, learn=not args.no_learn)

    if not results:
        print(_c("nothing found", YELLOW))
    for r in results:
        tag = {"both": _c("=", GREEN), "clerk": _c("|", CYAN),
               "cortex": _c("~", YELLOW)}[r.source]
        print(f"{tag} {_c(r.name, BOLD)}  {_c(r.chunk_id, DIM)}")
        print(f"  {r.preview}")
    if args.verbose:
        print(_c(f"\n  clerk {len(outcome.clerk_ids)} hits "
                 f"({outcome.ms_clerk:.1f}ms) | "
                 f"cortex {len(outcome.cortex_ids)} ({outcome.ms_cortex:.1f}ms) | "
                 f"recall@k {outcome.recall_at_k:.2f} | "
                 f"cortex-only {len(outcome.cortex_only_verified)}", DIM))
    bag.close()
    return 0


def cmd_status(args) -> int:
    bag = Bag(args.bag)
    print(json.dumps(bag.status(), indent=2))
    bag.close()
    return 0


def cmd_consolidate(args) -> int:
    bag = Bag(args.bag)
    print(json.dumps(bag.consolidate()))
    bag.close()
    return 0


def cmd_dream(args) -> int:
    bag = Bag(args.bag)
    for t in bag.dream(n_towers=args.towers):
        terms = ", ".join(t["terms"]) or "-"
        print(f"{_c('tower ' + str(t['tower']), BOLD)} "
              f"{_c('(' + str(t['size']) + ' chunks)', DIM)}")
        print(f"  {terms}")
        if t["files"]:
            print(_c("  " + " · ".join(t["files"]), DIM))
    bag.close()
    return 0


def cmd_forget(args) -> int:
    bag = Bag(args.bag)
    print(json.dumps(bag.forget()))
    bag.close()
    return 0


def cmd_rebuild(args) -> int:
    bag = Bag(args.bag)
    print("rebuilding from the ledger...")
    print(json.dumps(bag.rebuild(cortex_only=args.cortex_only)))
    bag.close()
    return 0


def cmd_relearn(args) -> int:
    bag = Bag(args.bag)
    for i, m in enumerate(bag.relearn(passes=args.passes), 1):
        print(f"pass {i}: recall@k {m.get('recall_at_k', 0):.3f}  "
              f"cortex-only {m.get('cortex_only', 0):.2f}")
    bag.close()
    return 0


def cmd_eval(args) -> int:
    bag = Bag(args.bag)
    print(json.dumps(bag.evaluate(limit=args.limit), indent=2))
    bag.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hermione",
        description="A bag you drop everything into. It sorts itself out.")
    p.add_argument("--bag", type=Path, default=DEFAULT_BAG,
                   help=f"bag location (default {DEFAULT_BAG})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a bag")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("add", help="drop files or folders in")
    s.add_argument("paths", nargs="+")
    s.add_argument("--vault", default="",
                   help="[1b] name the vault these bytes live in")
    s.set_defaults(fn=cmd_add)

    # --- iteration 1d ---
    s = sub.add_parser("ask", help="talk to it")
    s.add_argument("question", nargs="+")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_ask)

    # --- iteration 1b ---
    s = sub.add_parser("vault", help="attach or list vaults")
    s.add_argument("name", nargs="?")
    s.add_argument("path", nargs="?")
    s.add_argument("--detach", metavar="NAME")
    s.set_defaults(fn=cmd_vault)

    s = sub.add_parser("unreachable",
                       help="what you know about but can't open")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.set_defaults(fn=cmd_unreachable)

    # --- iteration 1c ---
    s = sub.add_parser("history", help="what happened, over a window")
    s.add_argument("-w", "--window", default="week",
                   help="today | yesterday | week | month | quarter | year | ever")
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser("note", help="drop a note in")
    s.add_argument("text", nargs="*")
    s.add_argument("--name", default="note")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("find", help="search (races both layers)")
    s.add_argument("query", nargs="+")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--no-learn", action="store_true")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("status", help="what is in the bag")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("consolidate", help="fast weights -> slow weights")
    s.set_defaults(fn=cmd_consolidate)

    s = sub.add_parser("dream", help="cluster into towers, surface structure")
    s.add_argument("-t", "--towers", type=int, default=8)
    s.set_defaults(fn=cmd_dream)

    s = sub.add_parser("forget", help="decay unused weights")
    s.set_defaults(fn=cmd_forget)

    s = sub.add_parser("rebuild", help="rebuild indexes from the ledger")
    s.add_argument("--cortex-only", action="store_true")
    s.set_defaults(fn=cmd_rebuild)

    s = sub.add_parser("relearn", help="replay the query log as training")
    s.add_argument("-p", "--passes", type=int, default=3)
    s.set_defaults(fn=cmd_relearn)

    s = sub.add_parser("eval", help="the built-in benchmark")
    s.add_argument("-n", "--limit", type=int, default=None)
    s.set_defaults(fn=cmd_eval)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
