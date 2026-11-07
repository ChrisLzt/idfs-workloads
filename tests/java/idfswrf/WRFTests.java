package idfswrf;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.Arrays;
import java.util.function.LongSupplier;

/** Exercises the real sampler with virtual extents; never opens a data file. */
public final class WRFTests {
    public static final long G=4194304L, CAPACITY=12292*G;
    public static final class Clock implements LongSupplier {
        public long now;
        public long getAsLong(){return now;}
    }
    public static void check(boolean ok,String message){if(!ok)throw new AssertionError(message);}
    public static Path fixture(Path dir)throws IOException {
        String text="WRF_ZIPF1\t"+CAPACITY+"\t4194304\t600000000\t1000000\t1048576\t0.99\t4\n"
            +"F\tinput\t0\t"+(G+4096)+"\t7\n"
            +"F\thistory_00h\t"+(2*G)+"\t"+(4096*G-4096)+"\t11\n"
            +"F\tboundary\t"+(4098*G)+"\t"+(G+4096)+"\t13\n"
            +"F\tcheckpoint_05h\t"+(4100*G)+"\t"+(4096*G-4096)+"\t17\n"
            +"F\thistory_06h\t"+(8196*G)+"\t"+(4096*G-4096)+"\t11\n"
            +"P\tstartup\t0\t60000000\nC\tinput\tread\t1\n"
            +"P\thistory_00h\t60000000\t150000000\nC\thistory_00h\twrite\t99\nC\tboundary\tread\t1\n"
            +"P\tcheckpoint_05h\t150000000\t510000000\nC\tcheckpoint_05h\twrite\t99\nC\tboundary\tread\t1\n"
            +"P\thistory_06h\t510000000\t600000000\nC\thistory_06h\twrite\t1\n";
        Path path=dir.resolve("wrf.timeline");Files.write(path,text.getBytes(StandardCharsets.UTF_8));return path;
    }
    private static void bounds(Timeline.Sample s){
        long[] starts={0,2*G,4098*G,4100*G,8196*G};
        long[] lengths={G+4096,4096*G-4096,G+4096,4096*G-4096,4096*G-4096};
        boolean legal=false;
        for(int i=0;i<starts.length;i++)if(s.offset>=starts[i]&&s.offset+s.size<=starts[i]+lengths[i])legal=true;
        check(legal&&s.offset%4096==0&&s.size>0&&s.size%4096==0&&s.size<=1048576,"legal file extent and direct-I/O alignment");
        check(s.offset/G==(s.offset+s.size-1)/G,"a request stays within its chosen object");
    }
    public static void main(String[]args)throws Exception {
        Path dir=Files.createTempDirectory(Paths.get(System.getProperty("java.io.tmpdir")),"wrf-sampler-");
        Path path=fixture(dir);Timeline t=Timeline.load(path,42);Timeline.Sample s=new Timeline.Sample();
        check(t.capacity()==CAPACITY&&t.maximum()==1048576&&t.duration()==600000000,"metadata");
        boolean tail=false;
        for(int i=0;i<2000;i++){check(t.next(0,s)&&s.read&&s.offset<2*G,"startup reads only input");bounds(s);tail|=s.size==4096;}
        check(tail,"short final object uses its actual valid extent");
        t.next(59999999,s);check(s.read&&s.offset<2*G,"last startup microsecond");
        int writes=0,reads=0;
        for(int i=0;i<10000;i++){
            check(t.next(60000000,s),"0h phase exists");bounds(s);
            if(s.read){reads++;check(s.offset>=4098*G&&s.offset<4100*G,"only boundary read in 0h phase");}
            else{writes++;check(s.offset>=2*G&&s.offset<4098*G,"only initial history write");}
        }
        check(reads>50&&reads<160&&writes+reads==10000,"small boundary read share survives mixed sampling");
        t.next(149999999,s);bounds(s);
        for(int i=0;i<1000;i++){
            t.next(150000000,s);bounds(s);
            check(s.read?(s.offset>=4098*G&&s.offset<4100*G):(s.offset>=4100*G&&s.offset<8196*G),"exact checkpoint boundary");
        }
        t.next(509999999,s);bounds(s);
        t.next(510000000,s);check(!s.read&&s.offset>=8196*G,"exact final-history boundary");
        t.next(599999999,s);bounds(s);check(!t.next(600000000,s),"600s stop");
        try{t.next(599999999,s);throw new AssertionError("reversed clock accepted");}catch(IllegalArgumentException expected){}

        // Different prior work and request rates must not reseed a phase or move its ranks.
        Timeline dense=Timeline.load(path,71),sparse=Timeline.load(path,71);
        Timeline.Sample a=new Timeline.Sample(),b=new Timeline.Sample();
        dense.next(0,a);for(int i=0;i<1000;i++)sparse.next(0,b);
        for(int i=0;i<10000;i++){
            dense.next(60000000L+i,a);sparse.next(60000000L+i*8000,b);
            check(a.offset==b.offset&&a.size==b.size&&a.read==b.read,"same sampling law across IOPS and one-second boundaries");
        }

        Timeline hot=Timeline.load(path,123);int[] counts=new int[4096],later=new int[4096];
        for(int i=0;i<200000;i++){hot.next(60000000,s);bounds(s);if(!s.read)counts[(int)((s.offset-2*G)/G)]++;}
        for(int i=0;i<200000;i++){hot.next(510000000,s);bounds(s);later[(int)((s.offset-8196*G)/G)]++;}
        int peak=0,peakLater=0,seen=0,total=0;
        for(int i=0;i<4096;i++){if(counts[i]>counts[peak])peak=i;if(later[i]>later[peakLater])peakLater=i;if(counts[i]>0)seen++;total+=counts[i];}
        check(peak==peakLater,"history snapshots retain the same relative object ranking");
        check(seen>3500,"all-object Zipf has an observable cold tail, not a fixed hot pool");
        Arrays.sort(counts);long hottest=0;for(int i=4096-820;i<4096;i++)hottest+=counts[i];
        double z=0,top=0;for(int rank=1;rank<=4096;rank++){double w=Math.pow(rank,-.99);z+=w;if(rank<=820)top+=w;}
        check(Math.abs((double)hottest/total-top/z)<.02,"observed top20 mass follows Zipf0.99");

        String original=new String(Files.readAllBytes(path),StandardCharsets.UTF_8);
        String[] bad={original.replace("\t0.99\t4", "\t0.5\t4"),
                      original.replace("F\thistory_00h\t"+(2*G),"F\thistory_00h\t0"),
                      original.replace("P\thistory_00h\t60000000", "P\thistory_00h\t61000000"),
                      original.replace("C\tboundary\tread\t1", "C\tboundary\tread\tNaN")};
        for(String invalid:bad){Files.write(path,invalid.getBytes(StandardCharsets.UTF_8));try{Timeline.load(path,42);throw new AssertionError("invalid timeline accepted");}catch(IOException expected){}}
        System.out.println("WRF_SAMPLER_TESTS_PASS boundaries exclusive_objects tails Zipf0.99 fixed_ranks no_quota no_physical_io=true");
    }
}
