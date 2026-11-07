package idfsgraphzipf;

/** Pure address generation: no file handles, native I/O, or dataset creation. */
public final class ZipfTests {
    public static void check(boolean ok, String why) {
        if (!ok) throw new AssertionError(why);
    }
    private static void invalid(ZipfDistribution d, double value) {
        try { d.rank(value); throw new AssertionError("invalid random variate accepted"); }
        catch (IllegalArgumentException expected) { }
    }
    public static void main(String[] args) {
        ZipfDistribution d = new ZipfDistribution(20261031L);
        check(d.rank(0) == 0, "first CDF bucket");
        check(d.rank(Math.nextAfter(1.0, 0.0)) == 1048575, "cold tail retained");
        check(Math.abs(d.cumulative(209715) - 0.8812691928671978) < 2e-12,
              "exact discrete Zipf mass, not bucket approximation");
        check(d.cumulative(1048575) == 1.0, "complete probability domain");
        invalid(d, -0.1); invalid(d, 1.0); invalid(d, Double.NaN);
        boolean[] seen = new boolean[1048576], hot = new boolean[1048576];
        for (int r = 0; r < seen.length; r++) {
            int object = d.objectAtRank(r);
            check(object >= 0 && object < seen.length && !seen[object], "mapping is a bijection");
            seen[object] = true;
            if (r < 209716) hot[object] = true;
        }
        for (int r : new int[] {1, 71, 10000, 1048574}) {
            check(d.rank((d.cumulative(r - 1) + d.cumulative(r)) / 2) == r, "CDF inverse");
            check(d.rank(d.cumulative(r)) == r + 1, "exact boundary selects next bucket");
        }
        ZipfDistribution.Stream a = d.stream(91L, false), b = d.stream(91L, false);
        ZipfDistribution.Stream other = d.stream(92L, false);
        boolean different = false;
        for (int i = 0; i < 4096; i++) {
            long off = a.nextOffset();
            check(off == b.nextOffset(), "same stream is reproducible");
            different |= off != other.nextOffset();
        }
        check(different, "independent streams must not clone one another");
        ZipfDistribution.Stream zipf = d.stream(71L, false), uniform = d.stream(72L, true);
        int hotZipf = 0, hotUniform = 0, visited = 0;
        boolean[] touched = new boolean[1048576];
        long maximum = 0;
        for (int i = 0; i < 200000; i++) {
            long off = zipf.nextOffset();
            check(off >= 0 && off + 32 <= 4398046511104L, "64-bit full-file bound");
            check(off % 32 == 0 && off % 4194304 + 32 <= 4194304, "32B in-object boundary");
            maximum = Math.max(maximum, off);
            int object = (int)(off / 4194304);
            if (!touched[object]) { touched[object] = true; visited++; }
            if (hot[object]) hotZipf++;
            if (hot[(int)(uniform.nextOffset() / 4194304)]) hotUniform++;
        }
        check(maximum > 2199023255552L, "no 32-bit offset truncation or fixed file partition");
        check(visited > 40000, "cold tail is sampled");
        check(Math.abs(hotZipf / 200000.0 - 0.8812691928671978) < 0.005, "Zipf request distribution");
        check(Math.abs(hotUniform / 200000.0 - 0.2) < 0.005, "uniform control uses same objects");
        System.out.println("GRAPH_ZIPF_SAMPLER_PASS requests=200000 no_storage_io=true");
    }
}
