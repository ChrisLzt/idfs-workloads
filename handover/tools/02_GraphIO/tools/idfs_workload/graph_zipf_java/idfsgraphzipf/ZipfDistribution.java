package idfsgraphzipf;

import java.util.SplittableRandom;

/** Immutable full-file distribution. Mapping identity is independent of request streams. */
public strictfp final class ZipfDistribution {
    public static final int OBJECTS = 1048576;
    public static final int OBJECT_BYTES = 4194304;
    public static final int TRANSFER_BYTES = 32;
    public static final long CAPACITY = 4398046511104L;
    private static final double[] CDF = makeCdf();
    private final int[] objects = new int[OBJECTS];

    private static double[] makeCdf() {
        double[] cdf = new double[OBJECTS];
        double sum = 0, correction = 0;
        for (int i = 0; i < OBJECTS; i++) {
            double value = StrictMath.pow(i + 1.0, -0.99) - correction;
            double next = sum + value;
            correction = (next - sum) - value;
            sum = next; cdf[i] = sum;
        }
        for (int i = 0; i < OBJECTS; i++) cdf[i] /= sum;
        cdf[OBJECTS - 1] = 1.0;
        return cdf;
    }

    public ZipfDistribution(long mappingSeed) {
        for (int i = 0; i < OBJECTS; i++) objects[i] = i;
        SplittableRandom random = new SplittableRandom(mappingSeed);
        for (int i = OBJECTS - 1; i > 0; i--) {
            int j = random.nextInt(i + 1), value = objects[i];
            objects[i] = objects[j]; objects[j] = value;
        }
    }

    public int rank(double uniform) {
        if (!(uniform >= 0 && uniform < 1))
            throw new IllegalArgumentException("random variate must be in [0,1)");
        int low = 0, high = OBJECTS - 1;
        while (low < high) {
            int middle = (low + high) >>> 1;
            if (uniform < CDF[middle]) high = middle;
            else low = middle + 1;
        }
        return low;
    }

    public int objectAtRank(int rank) { return objects[rank]; }
    public double cumulative(int rank) { return CDF[rank]; }
    public Stream stream(long seed, boolean uniform) { return new Stream(this, seed, uniform); }

    public static final class Stream {
        private final ZipfDistribution distribution;
        private final SplittableRandom random;
        private final boolean uniform;
        private Stream(ZipfDistribution distribution, long seed, boolean uniform) {
            this.distribution = distribution;
            this.random = new SplittableRandom(seed); this.uniform = uniform;
        }
        public long nextOffset() {
            int rank = uniform ? random.nextInt(OBJECTS) : distribution.rank(random.nextDouble());
            int slot = random.nextInt(OBJECT_BYTES / TRANSFER_BYTES);
            return (long)distribution.objects[rank] * OBJECT_BYTES + (long)slot * TRANSFER_BYTES;
        }
    }
}
