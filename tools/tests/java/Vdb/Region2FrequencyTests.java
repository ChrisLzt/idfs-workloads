package Vdb;

import User.UserCmd;
import idfsbaleen.TimelineWorkload;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.zip.GZIPInputStream;

/** Virtual clock and real stock UserCmd;only access metadata is opened. */
public final class Region2FrequencyTests {
    private static final long CAPACITY=5497553944576L;
    private static void check(boolean valid,String message){if(!valid)throw new AssertionError(message);}
    private static String key(long offset,long size,boolean read){return offset+":"+size+":"+read;}

    public static void main(String[] args)throws Exception {
        Path timeline=Paths.get(args[0]);final long[] now={0};
        TimelineWorkload user=new TimelineWorkload(()->now[0]);
        check(user.parser(new String[]{timeline.toUri().toString(),"seed20261026"}),"User parser");
        user.loadForCapacity(CAPACITY,44826624);
        List<String> rows=Files.readAllLines(timeline,StandardCharsets.UTF_8);
        check(rows.size()==601,"600 native slices");
        long total=0,totalReads=0,totalWrites=0,totalBytes=0,largest=0;
        Cmd_entry cmd=new Cmd_entry();UserCmd callback=new UserCmd(cmd);
        for(int second=0;second<600;second++){
            String[] row=rows.get(second+1).split("\t");
            Path path=timeline.getParent().resolve(row[2]);
            Map<String,Long> expected=new HashMap<>();
            long count,bytes,reads,writes;
            try(DataInputStream in=new DataInputStream(new BufferedInputStream(new GZIPInputStream(Files.newInputStream(path))))){
                check(in.readInt()==0x42465231&&in.readInt()==1,"frequency format");
                check(in.readLong()==CAPACITY&&in.readInt()==4194304&&in.readInt()==second,"table identity");
                count=in.readLong();bytes=in.readLong();int entries=in.readInt();reads=in.readLong();writes=in.readLong();
                for(int i=0;i<entries;i++){
                    long offset=in.readLong();int size=in.readInt(),op=in.readInt();long n=in.readLong();
                    check(expected.put(key(offset,size,op==1),n)==null,"unique native entry");
                }
                check(in.read()==-1,"no trailing data");
            }
            long seenReads=0,seenBytes=0;
            // One full native cycle per virtual slice is an offline assertion,
            // not a request quota or a schedule installed into the workload.
            now[0]=second*1000000L;
            for(long i=0;i<count;i++){
                user.preIO(callback);callback.updateCommand();
                check(cmd.cmd_lba>=0&&cmd.cmd_lba+cmd.cmd_xfersize<=CAPACITY,"64bit address in container");
                String address=key(cmd.cmd_lba,cmd.cmd_xfersize,cmd.cmd_read_flag);
                Long left=expected.get(address);check(left!=null&&left>0,"native address/size/operation frequency");
                if(left==1)expected.remove(address);else expected.put(address,left-1);
                if(cmd.cmd_read_flag)seenReads++;seenBytes+=cmd.cmd_xfersize;largest=Math.max(largest,cmd.cmd_lba);
            }
            check(expected.isEmpty()&&seenReads==reads&&count-seenReads==writes&&seenBytes==bytes,"complete native cycle");
            total+=count;totalReads+=reads;totalWrites+=writes;totalBytes+=bytes;
        }
        check(total==4570814L&&totalReads==3925006L&&totalWrites==645808L,"full source totals");
        check(totalBytes==10686382080000L&&largest>(1L<<32),"full byte total and large offsets");
        System.out.println("REGION2_USER_PASS requests="+total+" reads="+totalReads+" writes="+totalWrites+
                           " bytes="+totalBytes+" slices=600 no_physical_io=true");
    }
}
