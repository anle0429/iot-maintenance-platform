# Generator throughput and memory measurements

Pilot measurements establish how the bounded generator behaves before requesting a large source. Each receipt records actual stored bytes, elapsed time, configuration and observed memory; the validator rereads the resulting source rather than trusting writer counters.

The physical history remains continuous as the time horizon grows. Transport-fault selection and the arrival heap are bounded operations whose resource behavior must also be measured. Throughput projections are estimates tied to the measured machine/configuration, not proof that a 100 GB run completed.

The large-source workflow later generated and verified 102,447,847,856 offline bytes. That native receipt supersedes a projection for scale completion while leaving the earlier pilot measurements intact.

## Commands and recorded examples

Run commands from the repository root unless the example explicitly changes the working directory. Use a fresh output/run name and check the linked runbook before starting platform services. Examples belong to the recorded workload on this page.

```powershell
./scripts/measure-generator.ps1 -RunName throughput-practice
Get-Content outputs/throughput-practice/generator-throughput.json
```

## Implementation and evidence

- [Source: scripts/measure-generator.ps1](../scripts/measure-generator.ps1)
- [Recorded artifact: day2-throughput/generator-throughput.json](evidence/day2-throughput/generator-throughput.json)
- [Recorded artifact: day2-throughput/shard-01-quality.json](evidence/day2-throughput/shard-01-quality.json)
- [Recorded artifact: day2-throughput/shard-02-quality.json](evidence/day2-throughput/shard-02-quality.json)
- [Recorded artifact: day2-throughput/shard-03-quality.json](evidence/day2-throughput/shard-03-quality.json)
- [Recorded artifact: day2-throughput/shard-04-quality.json](evidence/day2-throughput/shard-04-quality.json)

## Related documentation

[Current architecture](architecture.md) · [Demo and recovery](demo-walkthrough.md) · [Rubric index](rubric-tracker.md)

[Original run notes](https://github.com/anle0429/iot-maintenance-platform/blob/f4da7bd21667a8bfca5715a7456bc2bcc813b0fc/docs/generator-throughput.md) retain the full historical narrative. Raw evidence files have not been translated or rewritten.
