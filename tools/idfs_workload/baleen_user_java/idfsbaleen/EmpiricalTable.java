package idfsbaleen;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.GZIPInputStream;

/** Joint original address, aligned size and operation; exact randomized cycles. */
public final class EmpiricalTable {
    public static final class Sample { public long offset; public int size; public boolean read; }
    private static final long G=4194304L;
    private final long capacity;
    private final int second,maximum;
    private final long[] offsets;
    private final int[] sizes,order;
    private final boolean[] reads;
    private final int[][][] groups;
    private final Random random;
    private int cursor,rotation;

    private EmpiricalTable(long capacity,int second,long[] offsets,int[] sizes,boolean[] reads,int[] order,int maximum,long seed){
        this.capacity=capacity;this.second=second;this.offsets=offsets;this.sizes=sizes;
        this.reads=reads;this.order=order;this.maximum=maximum;cursor=order.length;
        random=new Random(seed^(0x9e3779b97f4a7c15L*(second+1)));
        // Sparse groups support arbitrary transfer sizes and cross-object I/O.
        TreeMap<Long,TreeMap<Long,List<Integer>>> objects=new TreeMap<Long,TreeMap<Long,List<Integer>>>();
        for(int id:order){
            long object=offsets[id]/G,kind=2L*sizes[id]+(reads[id]?0:1);
            TreeMap<Long,List<Integer>> kinds=objects.get(object);
            if(kinds==null){kinds=new TreeMap<Long,List<Integer>>();objects.put(object,kinds);}
            List<Integer> members=kinds.get(kind);
            if(members==null){members=new ArrayList<Integer>();kinds.put(kind,members);}
            members.add(id);
        }
        groups=new int[objects.size()][][];int objectIndex=0;
        for(TreeMap<Long,List<Integer>> kinds:objects.values()){
            int[][] group=new int[kinds.size()][];int kindIndex=0;
            for(List<Integer> members:kinds.values()){
                int[] ids=new int[members.size()];for(int i=0;i<ids.length;i++)ids[i]=members.get(i);
                group[kindIndex++]=ids;
            }
            groups[objectIndex++]=group;
        }
    }

    public static EmpiricalTable load(Path path,long seed)throws IOException {
        try(InputStream raw=Files.newInputStream(path);
            InputStream decoded=path.toString().endsWith(".gz")?new GZIPInputStream(raw):raw;
            DataInputStream in=new DataInputStream(new BufferedInputStream(decoded))){
            if(in.readInt()!=0x42465231||in.readInt()!=1)throw new IOException("Unknown Baleen frequency format");
            long capacity=in.readLong();int grain=in.readInt(),second=in.readInt();
            long events=in.readLong(),bytes=in.readLong();int entries=in.readInt();long readCount=in.readLong(),writeCount=in.readLong();
            if(capacity<=0||capacity%G!=0||grain!=G||second<0||second>=600||entries<1||events<entries||events>Integer.MAX_VALUE
                    ||bytes<=0||readCount<0||writeCount<0||readCount+writeCount!=events)
                throw new IOException("Invalid Baleen frequency metadata");
            long[] offsets=new long[entries];int[] sizes=new int[entries],order=new int[(int)events];boolean[] reads=new boolean[entries];
            int cursor=0,maximum=0;long observedBytes=0,observedReads=0;
            for(int i=0;i<entries;i++){
                long offset=in.readLong();int size=in.readInt(),op=in.readInt();long count=in.readLong();
                if(offset<0||offset%4096!=0||size<=0||size%4096!=0||offset>capacity-size||op<1||op>2
                        ||count<=0||count>events-cursor
                        ||(i>0&&(offset<offsets[i-1]||(offset==offsets[i-1]&&(size<sizes[i-1]
                            ||(size==sizes[i-1]&&op<=(reads[i-1]?1:2)))))))
                    throw new IOException("Invalid Baleen address/size/operation/multiplicity");
                offsets[i]=offset;sizes[i]=size;reads[i]=op==1;maximum=Math.max(maximum,size);
                for(int j=0;j<(int)count;j++)order[cursor++]=i;
                observedBytes+=count*size;if(op==1)observedReads+=count;
            }
            if(cursor!=events||observedBytes!=bytes||observedReads!=readCount||in.read()!=-1)
                throw new IOException("Baleen frequency totals differ");
            return new EmpiricalTable(capacity,second,offsets,sizes,reads,order,maximum,seed);
        }
    }

    private void balancedOrder(){
        long[] global=new long[order.length];int output=0;
        for(int[][] group:groups){
            int amount=0;for(int[] ids:group)amount+=ids.length;
            long[] local=new long[amount];int position=0;long objectPhase=random.nextInt()&0x7fffffffL;
            for(int[] ids:group){
                for(int i=ids.length-1;i>0;i--){int j=random.nextInt(i+1),saved=ids[i];ids[i]=ids[j];ids[j]=saved;}
                long phase=random.nextInt()&0x7fffffffL;
                for(int i=0;i<ids.length;i++){
                    long time=(i*(1L<<31)+phase)/ids.length;
                    local[position++]=(time<<32)|(ids[i]&0xffffffffL);
                }
            }
            Arrays.sort(local);
            for(int i=0;i<amount;i++){
                long time=(i*(1L<<31)+objectPhase)/amount;
                global[output++]=(time<<32)|(local[i]&0xffffffffL);
            }
        }
        if(output!=order.length)throw new IllegalStateException("Lost native Baleen requests");
        Arrays.sort(global);for(int i=0;i<order.length;i++)order[i]=(int)global[i];
        // Uniform rotation preserves native marginals even for partial cycles.
        rotation=random.nextInt(order.length);
    }

    public long capacity(){return capacity;}
    public int second(){return second;}
    public int maximum(){return maximum;}
    public int requests(){return order.length;}
    public void next(Sample result){
        if(cursor==order.length){balancedOrder();cursor=0;}
        int id=order[(int)(((long)rotation+cursor++)%order.length)];
        result.offset=offsets[id];result.size=sizes[id];result.read=reads[id];
    }
}
