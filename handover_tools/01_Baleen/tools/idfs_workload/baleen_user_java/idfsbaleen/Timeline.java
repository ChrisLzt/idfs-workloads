package idfsbaleen;

import java.io.*;
import java.net.URI;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** One wall-clock timeline: no I/O-count quotas and no catch-up of expired slices. */
public final class Timeline {
    private long capacity,duration,last=-1;
    private int maximum;
    private long[] starts,ends;
    private EmpiricalTable[] tables;
    private EmpiricalTable.Sample[] first;
    private boolean[] prefetched;

    public static Timeline load(Path path,long seed)throws IOException {
        List<String> rows=Files.readAllLines(path,StandardCharsets.UTF_8);
        if(rows.isEmpty())throw new IOException("Empty Baleen timeline");
        try{
            String[] header=rows.get(0).split("\t",-1);
            if(header.length!=5||!header[0].equals("BALEEN_TIMELINE1")||Long.parseLong(header[2])!=4194304L)
                throw new IOException("Invalid Baleen timeline header");
            Timeline result=new Timeline();result.capacity=Long.parseLong(header[1]);result.duration=Long.parseLong(header[3]);int n=Integer.parseInt(header[4]);
            if(result.capacity<=0||result.duration<=0||n<1||n>600||rows.size()!=n+1)throw new IOException("Invalid Baleen timeline extent");
            result.starts=new long[n];result.ends=new long[n];result.tables=new EmpiricalTable[n];
            result.first=new EmpiricalTable.Sample[n];result.prefetched=new boolean[n];long end=0;
            for(int i=0;i<n;i++){
                String[] fields=rows.get(i+1).split("\t",-1);if(fields.length!=3)throw new IOException("Invalid Baleen timeline row");
                long start=Long.parseLong(fields[0]),next=Long.parseLong(fields[1]);
                if(start!=end||next<=start||next>result.duration)throw new IOException("Baleen timeline gap or overlap");
                URI location=URI.create(fields[2]);Path source=location.getScheme()==null?path.toAbsolutePath().getParent().resolve(fields[2]).normalize():Paths.get(location);
                EmpiricalTable table=EmpiricalTable.load(source,seed);
                if(table.capacity()!=result.capacity||table.second()!=i)throw new IOException("Baleen table identity differs");
                result.starts[i]=start;result.ends[i]=next;result.tables[i]=table;result.maximum=Math.max(result.maximum,table.maximum());
                result.first[i]=new EmpiricalTable.Sample();table.next(result.first[i]);result.prefetched[i]=true;end=next;
            }
            if(end!=result.duration)throw new IOException("Incomplete Baleen timeline");
            return result;
        }catch(IllegalArgumentException bad){throw new IOException("Invalid Baleen timeline",bad);}
    }
    public long capacity(){return capacity;}
    public long duration(){return duration;}
    public int maximum(){return maximum;}
    public synchronized boolean next(long now,EmpiricalTable.Sample sample){
        if(now<0||now<last)throw new IllegalArgumentException("Elapsed clock reversed");
        last=now;if(now>=duration)return false;
        int found=Arrays.binarySearch(starts,now),index=found>=0?found:-found-2;
        if(index<0||now>=ends[index])throw new IllegalStateException("Missing native Baleen time span");
        if(prefetched[index]){sample.offset=first[index].offset;sample.size=first[index].size;sample.read=first[index].read;prefetched[index]=false;}
        else tables[index].next(sample);
        return true;
    }
}
