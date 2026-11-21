package idfscommon;

import User.UserClass;
import User.UserCmd;
import User.UserDeviceInfo;
import User.WorkloadInfo;
import java.util.concurrent.atomic.AtomicLong;

/** Finite write pass over a dense file or the payload regions of a sparse file. */
public final class ContainerPrepare extends UserClass {
    private static final int ALIGNMENT = 4096;
    private long capacity;
    private long stride;
    private long payloadOffset;
    private int transfer;
    private long first;
    private long end;
    private WorkloadInfo work;
    private final AtomicLong completed = new AtomicLong();
    private long expectedBytes;

    @Override public boolean parser(String[] args) {
        if (args.length != 7) return false;
        try {
            capacity = number(args[0], "extent");
            stride = number(args[1], "stride");
            payloadOffset = number(args[2], "offset");
            transfer = Math.toIntExact(number(args[3], "payload"));
            first = number(args[4], "first");
            end = number(args[5], "end");
            boolean dense = payloadOffset == 0 && transfer == stride;
            if (capacity <= 0 || stride <= 0 || transfer <= 0 || payloadOffset < 0
                    || capacity % ALIGNMENT != 0 || stride % ALIGNMENT != 0
                    || transfer % ALIGNMENT != 0 || payloadOffset % ALIGNMENT != 0
                    || payloadOffset > stride - transfer
                    || (!dense && capacity % stride != 0)
                    || first < 0 || end <= first || end > (capacity - 1) / stride + 1) {
                return false;
            }
            long finalAddress = address(end - 1);
            expectedBytes = (end - first - 1) * transfer + sizeAt(finalAddress);
            return RuntimeIdentity.matchesJar(ContainerPrepare.class, args[6]);
        } catch (Exception badParameters) {
            return false;
        }
    }

    private static long number(String value, String prefix) {
        if (!value.startsWith(prefix)) throw new IllegalArgumentException(value);
        return Long.parseLong(value.substring(prefix.length()));
    }

    @Override public UserDeviceInfo initialize(WorkloadInfo info) {
        if (info.getSd().concatenated_sd || info.getSd().end_lba != capacity
                || info.getRdPct() != 0 || info.getSeekPct() >= 0
                || info.getAverageXferSize() != transfer
                || (first == 0 && payloadOffset == 0 && !info.getSd().canWeUseBlockZero())) {
            throw new IllegalArgumentException("prepare requires the exact regular-file SD and a write-only EOF run");
        }
        work = info;
        return super.initialize(info);
    }

    @Override public boolean generate() {
        work.setFirstStartDelta(0);
        for (long slot = first; slot < end; slot++) {
            long lba = address(slot);
            if (isWorkloadDone() || !work.scheduleIO(0, lba, sizeAt(lba), false)) return false;
        }
        return true;
    }

    private long address(long slot) { return slot * stride + payloadOffset; }

    private int sizeAt(long lba) { return (int)Math.min(transfer, capacity - lba); }

    @Override public boolean preIO(UserCmd command) { return true; }

    @Override public boolean postIO(UserCmd command) {
        if (completed.incrementAndGet() == end - first) {
            publishCompletion();
            // Vdbench subtracts this request from its sequential in-flight
            // count after postIO returns. Set EOF while one request is in flight.
            work.setEOF(end - first);
        }
        return true;
    }

    private void publishCompletion() {
        sendMessageToSummary("IDFS_PREPARE_COMPLETE %s %s %s", Long.toString(first),
                Long.toString(end), Long.toString(expectedBytes));
    }

    @Override public void passData(String data) { }
}
