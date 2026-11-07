package idfsai;

import User.*;
import java.io.IOException;
import java.net.URI;
import java.nio.file.*;
import java.util.function.LongSupplier;

/** Stock5.04.06 User extension; ordinary single SD, max rate, no request quotas. */
public final class TimelineWorkload extends UserClass {
    private static final class FirstIOClock implements LongSupplier {
        private long start;
        private boolean begun;
        public long getAsLong() {
            long now = System.nanoTime();
            if (!begun) { start = now; begun = true; }
            return (now - start) / 1000;
        }
    }
    private final LongSupplier clock;
    private Path manifest;
    private long seed, last = -1, late;
    private boolean zipf;
    private Timeline timeline;
    private WorkloadInfo work;
    private final Timeline.Sample sample = new Timeline.Sample();
    public TimelineWorkload() { this(new FirstIOClock()); }
    public TimelineWorkload(LongSupplier clock) {
        if (clock == null) throw new IllegalArgumentException("clock");
        this.clock = clock;
    }
    @Override public boolean parser(String[] args) {
        if (args.length != 3 || !(args[1].equals("current") || args[1].equals("baseline"))) return false;
        try {
            URI uri = URI.create(args[0]);
            manifest = uri.getScheme() == null ? Paths.get(args[0]) : Paths.get(uri);
            zipf = args[1].equals("current");
            seed = Long.parseLong(args[2].startsWith("seed") ? args[2].substring(4) : args[2]);
            return Files.isRegularFile(manifest);
        } catch (IllegalArgumentException bad) { return false; }
    }
    public void loadForCapacity(long capacity, int buffer) {
        try { timeline = Timeline.load(manifest, seed, zipf); }
        catch (IOException bad) { throw new IllegalArgumentException("Invalid SES container timeline", bad); }
        if (timeline.capacity() != capacity || timeline.maximum() > buffer)
            throw new IllegalArgumentException("SES container capacity or I/O buffer differs");
    }
    @Override public UserDeviceInfo initialize(WorkloadInfo info) {
        loadForCapacity(info.getSd().end_lba, info.getAverageXferSize());
        work = info; return super.initialize(info);
    }
    @Override public boolean generate() {
        if (work == null || timeline == null) throw new IllegalStateException("Not initialized");
        work.setFirstStartDelta(0); long generated = 0;
        while (!isWorkloadDone()) {
            if (!work.scheduleIO(0, 0, timeline.maximum(), true)) break;
            generated++;
        }
        work.setEOF(generated); return generated > 0;
    }
    public synchronized long lateRequests() { return late; }
    @Override public synchronized boolean preIO(UserCmd cmd) {
        if (timeline == null) throw new IllegalStateException("Not initialized");
        long now = clock.getAsLong();
        if (now < 0 || now < last) throw new IllegalArgumentException("Elapsed clock reversed");
        last = now;
        if (now >= timeline.duration()) { late++; timeline.finalSample(sample); }
        else if (!timeline.next(now, sample)) throw new IllegalStateException("Missing active SES slice");
        cmd.setLba(sample.offset); cmd.setXfersize(sample.size); cmd.setReadFlag(sample.read);
        return true;
    }
    @Override public boolean postIO(UserCmd cmd) { return true; }
    @Override public void passData(String data) { }
}
