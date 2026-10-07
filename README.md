# monay

Five-agent bake-off for a feasible, provable, equitable, automated way to make more money than is spent.
See [JUDGING.md](JUDGING.md) for scores. **Winner: [`candidates/C-savings`](candidates/C-savings) (spendaudit)**, a local-first spending auditor that runs on your bank exports at $0 cost.

```
cd candidates/C-savings && python -m spendaudit audit <your exports> --out audit.md
```
