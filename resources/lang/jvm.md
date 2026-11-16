# Java / Kotlin (JVM) Performance Adapter

## 1. High-Resolution Clock & Memory APIs
- **Timer:** `System.nanoTime()` (monotonic nanosecond clock; never `System.currentTimeMillis()`).
- **Memory:** `Runtime.getRuntime()` (`totalMemory() - freeMemory()` delta across a dedicated post-timed pass after `System.gc()`, or `MemoryMXBean` heap usage) -> `peak_kb = deltaBytes / 1024.0`.
- **Sandbox Execution:** Run inside `S/sandbox/` (baseline) and `S/sandbox-opt/` (opt) using offline flags (`mvn -o` / `gradlew --offline`). Keep benchmark test class inside `src/test/java/.../VeriPerfOpt1BenchTest.java` inside the sandbox only.

## 2. Executing Benchmarks & Correctness Tests
- **Phase 2 Baseline (in `S/sandbox/`):**
  `python "<VP>" bench-run --s "$S" --id opt1 --phase baseline --cwd "$S/sandbox" -- mvn -o -B test -Dtest=VeriPerfOpt1BenchTest`
- **Phase 4 Dual Gate (in `S/sandbox-opt/`):**
  `python "<VP>" verify-opt --s "$S" --id opt1 --cwd "$S/sandbox-opt" --opt-bench-cmd "mvn -o -B test -Dtest=VeriPerfOpt1BenchTest" --test-cmd "mvn -o -B test -Dtest=!VeriPerf*"`

## 3. Harness Template (`S/sandbox[-opt]/src/test/java/.../VeriPerfOpt1BenchTest.java`)
```java
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.junit.jupiter.api.Test;

class VeriPerfOpt1BenchTest {
    public static volatile Object BLACKHOLE;

    @Test
    void runBench() throws Exception {
        int warmup = Integer.parseInt(System.getenv().getOrDefault("VERIPERF_WARMUP", "5"));
        int runs = Integer.parseInt(System.getenv().getOrDefault("VERIPERF_RUNS", "20"));
        String[] names = {"small", "medium", "large"};
        int[] sizes = {200, 2000, 10000};

        for (int t = 0; t < names.length; t++) {
            var input = buildInput(sizes[t]);
            Object out = null;
            for (int w = 0; w < warmup; w++) out = TargetService.run(input);
            System.gc();

            long[] samples = new long[runs];
            for (int r = 0; r < runs; r++) {
                long t0 = System.nanoTime();
                out = TargetService.run(input);
                samples[r] = System.nanoTime() - t0;
            }
            BLACKHOLE = out;

            Runtime rt = Runtime.getRuntime();
            System.gc();
            long m0 = rt.totalMemory() - rt.freeMemory();
            Object _m = TargetService.run(input);
            long m1 = rt.totalMemory() - rt.freeMemory();
            double peakKB = Math.max(0.0, (m1 - m0) / 1024.0);

            byte[] dig = MessageDigest.getInstance("SHA-256")
                .digest(String.valueOf(out).getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (int i = 0; i < 8; i++) hex.append(String.format("%02x", dig[i]));

            System.out.printf(Locale.ROOT,
                "VERIPERF_METRIC:{\"tier\":\"%s\",\"warmup\":%d,\"runs\":%d,\"samples_ns\":%s,\"peak_kb\":%.2f,\"result_hash\":\"%s\"}%n",
                names[t], warmup, runs, Arrays.toString(samples), peakKB, hex);
        }
    }
}
```

## 4. JVM Optimization Idioms & Anti-Illusion Traps
- **JIT compilation trap:** Ensure warm-up loops execute enough iterations (e.g., inside `warm` loop or >= 5 runs over medium/large input) for C2 JIT compilation; assign output to `volatile` blackhole + compute `result_hash`.
- **Idiomatic wins:** `HashMap`/`HashSet` instead of `List.contains()`, `StringBuilder` instead of `+` in loops, pre-compile `Pattern.compile(...)`, `BufferedReader` streams instead of `Files.readAllBytes`, avoid boxing (`int[]`/streams vs `List<Integer>`).
