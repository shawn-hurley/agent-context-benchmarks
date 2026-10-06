## Advanced patterns

### HTTP session for many queries

**User intent:** *"I need to run many queries interactively"*

```bash
rgctl -r "$REPO" serve --open
# Dashboard UI for exploration; agents should still prefer CLI structured verbs:
#   rgctl -f json find|callers|relations|inventory|status …
```

See [docs/guides/http-server-and-dashboard.md](../upstream/docs/guides/http-server-and-dashboard.md). For IDE agents spawn `rgctl -f json` subprocesses; optional `rgctl serve` for a local dashboard on one repo.
