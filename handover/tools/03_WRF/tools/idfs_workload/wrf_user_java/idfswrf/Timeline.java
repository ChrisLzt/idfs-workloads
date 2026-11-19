package idfswrf;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Four business activities, fixed all-object Zipf ranks, no rate or count quotas. */
public final class Timeline {
    public static final class Sample {public long offset;public int size;public boolean read;}
    private static final long G=4194304L;
    private static final class FileRegion {
        final long base,length;
        final int[] objects;
        final double[] cdf;
        FileRegion(long base,long length,long rankSeed){
            this.base=base;this.length=length;
            int n=(int)((length-1)/G+1);objects=new int[n];cdf=new double[n];
            Random ranks=new Random(rankSeed);double total=0;
            for(int i=0;i<n;i++){objects[i]=i;total+=Math.pow(i+1,-.99);cdf[i]=total;}
            for(int i=n-1;i>0;i--){int j=ranks.nextInt(i+1),saved=objects[i];objects[i]=objects[j];objects[j]=saved;}
        }
        void sample(Random random,int maximum,Sample result){
            double value=random.nextDouble()*cdf[cdf.length-1];
            int lo=0,hi=cdf.length-1;
            while(lo<hi){int mid=(lo+hi)>>>1;if(value<cdf[mid])hi=mid;else lo=mid+1;}
            long relative=objects[lo]*G,valid=Math.min(G,length-relative);
            int blocks=(int)((valid-1)/maximum+1),block=random.nextInt(blocks);
            long within=(long)block*maximum;
            result.offset=base+relative+within;result.size=(int)Math.min(maximum,valid-within);
        }
    }
    private static final class Channel {
        final FileRegion file;final boolean read;final double weight;
        Channel(FileRegion file,boolean read,double weight){this.file=file;this.read=read;this.weight=weight;}
    }
    private static final class Phase {
        final long start,end;final Random random;final List<Channel> channels=new ArrayList<Channel>();
        double total;
        Phase(long start,long end,long seed){this.start=start;this.end=end;random=new Random(seed);}
        void sample(int maximum,Sample result){
            double target=random.nextDouble()*total,cumulative=0;
            Channel selected=channels.get(channels.size()-1);
            for(Channel channel:channels){cumulative+=channel.weight;if(target<cumulative){selected=channel;break;}}
            selected.file.sample(random,maximum,result);result.read=selected.read;
        }
    }
    private final long capacity,duration,slice;
    private final int maximum;
    private final List<Phase> phases;
    private long last=-1;
    private Timeline(long capacity,long duration,long slice,int maximum,List<Phase> phases){
        this.capacity=capacity;this.duration=duration;this.slice=slice;this.maximum=maximum;this.phases=phases;
    }
    public static Timeline load(Path path,long seed)throws IOException {
        List<String> rows=Files.readAllLines(path,StandardCharsets.UTF_8);
        try{
            if(rows.isEmpty())throw new IOException("Empty WRF timeline");
            String[] h=rows.get(0).split("\t",-1);
            if(h.length!=8||!h[0].equals("WRF_ZIPF1")||Long.parseLong(h[2])!=G
                ||Long.parseLong(h[3])!=600000000||Long.parseLong(h[4])!=1000000
                ||Integer.parseInt(h[5])!=1048576||Double.parseDouble(h[6])!=.99||Integer.parseInt(h[7])!=4)
                throw new IOException("WRF requires 4MiB,600s,1s slices,1MiB maximum and Zipf0.99");
            long capacity=Long.parseLong(h[1]);
            if(capacity<=0||capacity%G!=0)throw new IOException("Invalid WRF capacity");
            Map<String,FileRegion> files=new LinkedHashMap<String,FileRegion>();
            List<Phase> phases=new ArrayList<Phase>();Set<String> names=new HashSet<String>();
            long fileEnd=0,phaseEnd=0;Phase current=null;
            for(int line=1;line<rows.size();line++){
                String[] fields=rows.get(line).split("\t",-1);
                if(fields[0].equals("F")){
                    if(fields.length!=5||current!=null||!fields[1].matches("[a-z][a-z0-9_]*")||files.containsKey(fields[1]))throw new IOException("Invalid WRF file row");
                    long base=Long.parseLong(fields[2]),length=Long.parseLong(fields[3]),rankSeed=Long.parseLong(fields[4]);
                    if(base!=fileEnd||base%G!=0||length<=0||length%4096!=0||length>capacity-base||(length-1)/G+1>1000000)
                        throw new IOException("Invalid WRF exclusive file extent");
                    long slots=((length-1)/G+1)*G;
                    if(slots>capacity-base)throw new IOException("WRF file slot exceeds container");
                    files.put(fields[1],new FileRegion(base,length,rankSeed));fileEnd=base+slots;
                }else if(fields[0].equals("P")){
                    if(fields.length!=4||!fields[1].matches("[a-z][a-z0-9_]*")||!names.add(fields[1]))throw new IOException("Invalid WRF phase row");
                    long start=Long.parseLong(fields[2]),end=Long.parseLong(fields[3]);
                    if(start!=phaseEnd||end-start<60000000||end>600000000||start%1000000!=0||end%1000000!=0
                        ||(current!=null&&current.channels.isEmpty()))throw new IOException("WRF phase gap,short stage or missing channels");
                    current=new Phase(start,end,seed^(0x9e3779b97f4a7c15L*(phases.size()+1)));phases.add(current);phaseEnd=end;
                }else if(fields[0].equals("C")){
                    if(fields.length!=4||current==null||!files.containsKey(fields[1])||!(fields[2].equals("read")||fields[2].equals("write")))
                        throw new IOException("Invalid WRF channel");
                    double weight=Double.parseDouble(fields[3]);
                    if(!(weight>0)||Double.isInfinite(weight)||Double.isNaN(weight)||Double.isInfinite(current.total+weight))throw new IOException("Invalid WRF channel weight");
                    current.channels.add(new Channel(files.get(fields[1]),fields[2].equals("read"),weight));current.total+=weight;
                }else throw new IOException("Unknown WRF timeline row");
            }
            if(fileEnd!=capacity||phases.size()!=4||phaseEnd!=600000000||current==null||current.channels.isEmpty())
                throw new IOException("Incomplete WRF file or activity inventory");
            return new Timeline(capacity,600000000,1000000,1048576,phases);
        }catch(IllegalArgumentException bad){throw new IOException("Invalid WRF timeline",bad);}
    }
    public long capacity(){return capacity;}
    public long duration(){return duration;}
    public int maximum(){return maximum;}
    public synchronized boolean next(long elapsedUs,Sample result){
        if(elapsedUs<0||elapsedUs<last)throw new IllegalArgumentException("Elapsed clock reversed");
        last=elapsedUs;if(elapsedUs>=duration)return false;
        long second=elapsedUs/slice*slice;
        for(Phase phase:phases)if(second>=phase.start&&second<phase.end){phase.sample(maximum,result);return true;}
        throw new IllegalStateException("Missing WRF activity");
    }
    /** Stock preIO cannot cancel an already-in-flight command at shutdown. */
    public synchronized void finalSample(Sample result){phases.get(phases.size()-1).sample(maximum,result);}
}
