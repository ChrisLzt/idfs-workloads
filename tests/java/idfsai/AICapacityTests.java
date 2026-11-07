package idfsai;

import java.io.IOException;
import java.nio.file.Paths;

/** Load only metadata; the ordinary container file is never opened. */
public final class AICapacityTests {
    public static void main(String[] args) throws Exception {
        for (boolean zipf : new boolean[]{true, false}) {
            Timeline timeline = Timeline.load(Paths.get(args[0]), 71, zipf);
            if (timeline.capacity() != 4947802324992L)
                throw new AssertionError("approved4.5TiB inventory must load");
            Timeline.Sample sample = new Timeline.Sample();
            if (!timeline.next(0, sample) || sample.offset < 0 ||
                sample.offset + sample.size > timeline.capacity())
                throw new AssertionError("64-bit sampled address must belong to approved inventory");
        }
        Timeline boundary = Timeline.load(Paths.get(args[1]), 71, true);
        if (boundary.capacity() != 6597069766656L)
            throw new AssertionError("exact6TiB boundary must load");
        try {
            Timeline.load(Paths.get(args[2]), 71, true);
            throw new AssertionError("inventory above6TiB must be rejected before allocation");
        } catch (IOException expected) {
            if (!expected.getMessage().contains("capacity")) throw expected;
        }
        System.out.println("AI_CAPACITY_TESTS_PASS approved4.5TiB bounded6TiB no_physical_io=true");
    }
}
