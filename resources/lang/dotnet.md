# C# (.NET) Performance Adapter

## 1. High-Resolution Clock & Memory APIs
- **Timer:** `Stopwatch.GetTimestamp()` converted via `(double)(t1 - t0) * 1_000_000_000.0 / Stopwatch.Frequency` (nanosecond precision).
- **Memory:** `GC.GetAllocatedBytesForCurrentThread()` (exact per-thread heap allocation bytes in .NET Core / .NET 5+) divided by `1024.0` for `peak_kb`.
- **Sandbox Execution:** Run in `Release` mode (`-c Release --no-restore`) inside `S/sandbox/` (baseline) and `S/sandbox-opt/` (opt). Restore NuGet cache offline once if `obj/` exists.

## 2. Executing Benchmarks & Correctness Tests
- **Phase 2 Baseline:**
  `python "<VP>" bench-run --s "$S" --id opt1 --phase baseline --cwd "$S/sandbox" -- dotnet test --no-restore -c Release --filter VeriPerfOpt1`
- **Phase 4 Dual Gate:**
  `python "<VP>" verify-opt --s "$S" --id opt1 --cwd "$S/sandbox-opt" --opt-bench-cmd "dotnet test --no-restore -c Release --filter VeriPerfOpt1" --test-cmd "dotnet test --no-restore --filter FullyQualifiedName!~VeriPerf"`

## 3. Harness Template (`S/sandbox[-opt]/tests/.../VeriPerfOpt1Tests.cs`)
```csharp
using System;
using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Xunit;

public class VeriPerfOpt1Tests
{
    private static volatile object? _sink;

    [Fact]
    public void RunVeriPerfOpt1()
    {
        int warmup = int.TryParse(Environment.GetEnvironmentVariable("VERIPERF_WARMUP"), out var w) ? w : 5;
        int runs = int.TryParse(Environment.GetEnvironmentVariable("VERIPERF_RUNS"), out var r) ? r : 20;
        var tiers = new (string name, int n)[] { ("small", 200), ("medium", 2000), ("large", 10000) };

        foreach (var (name, n) in tiers)
        {
            var input = BuildInput(n);
            object? outVal = null;
            for (int i = 0; i < warmup; i++) outVal = TargetService.Run(input);

            GC.Collect();
            var samplesNs = new long[runs];
            for (int i = 0; i < runs; i++)
            {
                long t0 = Stopwatch.GetTimestamp();
                outVal = TargetService.Run(input);
                long dt = Stopwatch.GetTimestamp() - t0;
                samplesNs[i] = (long)(dt * 1_000_000_000.0 / Stopwatch.Frequency);
            }
            _sink = outVal;

            long b0 = GC.GetAllocatedBytesForCurrentThread();
            _ = TargetService.Run(input);
            double peakKb = Math.Max(0, (GC.GetAllocatedBytesForCurrentThread() - b0) / 1024.0);

            byte[] hashBytes = SHA256.HashData(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(outVal)));
            string hashHex = Convert.ToHexString(hashBytes[..8]).ToLowerInvariant();
            string sJson = JsonSerializer.Serialize(samplesNs);
            Console.WriteLine(string.Format(CultureInfo.InvariantCulture,
                "VERIPERF_METRIC:{{\"tier\":\"{0}\",\"warmup\":{1},\"runs\":{2},\"samples_ns\":{3},\"peak_kb\":{4:F2},\"result_hash\":\"{5}\"}}",
                name, warmup, runs, sJson, peakKb, hashHex));
        }
    }
}
```

## 4. .NET Optimization Idioms & Anti-Illusion Traps
- **Always benchmark `-c Release`:** Debug mode disables JIT inlining and optimizations, producing misleading bottlenecks.
- **Avoid double LINQ enumeration:** Materialize or convert `.ToDictionary()` / `HashSet<T>` before loops; use `Span<T>` / `ReadOnlySpan<char>`, `StringBuilder`, compiled `[GeneratedRegex]`/`RegexOptions.Compiled`, and `AsNoTracking()` + batched queries in EF Core.
