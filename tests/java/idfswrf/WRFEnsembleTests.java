package idfswrf;

import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Exercise the complete ensemble address domain; no scientific data file is opened. */
public final class WRFEnsembleTests {
    private static final long G=4194304L;
    private static final class Region {
        String id,role;int member;long base,length;int[] ranks;double hotShare;
        Region(String[] row){
            id=row[1];base=Long.parseLong(row[2]);length=Long.parseLong(row[3]);
            if(id.startsWith("member_")){String[] p=id.split("_",3);member=Integer.parseInt(p[1]);role=p[2];}
            else{member=0;role=id;}
            int n=(int)((length-1)/G+1);ranks=new int[n];List<Integer> order=new ArrayList<Integer>();
            for(int i=0;i<n;i++)order.add(i);
            Collections.shuffle(order,new Random(Long.parseLong(row[4])));
            double z=0,top=0;
            for(int i=0;i<n;i++){ranks[order.get(i)]=i+1;double w=Math.pow(i+1,-.99);z+=w;if(i<(n+4)/5)top+=w;}
            hotShare=top/z;
        }
    }
    private static final class Phase {
        String name;long start;Map<String,Double> weights=new LinkedHashMap<String,Double>();
    }
    private static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception{
        Path path=Paths.get(args[0]);Timeline timeline=Timeline.load(path,123);
        check(timeline.capacity()==3417015582720L,"approved8-member capacity must be3.107757568TiB");
        Map<String,Region> regions=new LinkedHashMap<String,Region>();List<Phase> phases=new ArrayList<Phase>();Phase current=null;
        for(String line:Files.readAllLines(path,StandardCharsets.UTF_8)){
            String[] row=line.split("\t");
            if(row[0].equals("F")){Region region=new Region(row);regions.put(region.id,region);}
            else if(row[0].equals("P")){current=new Phase();current.name=row[1];current.start=Long.parseLong(row[2]);phases.add(current);}
            else if(row[0].equals("C"))current.weights.put(row[1],Double.parseDouble(row[3]));
        }
        check(regions.size()==42&&phases.size()==4,"42 identities andfouractivities");
        Timeline.Sample sample=new Timeline.Sample();int tableReads=0;
        for(int stage=0;stage<4;stage++){
            Phase phase=phases.get(stage);int[] members=new int[9];int hot=0,scientific=0;
            double total=0,target=0;
            for(Map.Entry<String,Double> e:phase.weights.entrySet()){total+=e.getValue();target+=e.getValue()*regions.get(e.getKey()).hotShare;}
            for(int i=0;i<50000;i++){
                check(timeline.next(phase.start+i*1000L,sample),"sampler remainsactive");Region selected=null;
                for(Region r:regions.values())if(sample.offset>=r.base&&sample.offset<r.base+r.length){selected=r;break;}
                check(selected!=null&&sample.offset+sample.size<=selected.base+selected.length,"exclusive validfile extent");
                check(sample.offset%4096==0&&sample.size>0&&sample.size%4096==0&&sample.size<=1048576,"directIO alignedrequest");
                check(sample.offset/G==(sample.offset+sample.size-1)/G,"oneobject request");
                check(phase.weights.containsKey(selected.id),"only currentactivity files");
                if(selected.member==0){check(stage==0&&sample.read,"sharedtables onlystartupreads");tableReads++;}
                else{members[selected.member]++;scientific++;}
                boolean wantRead=stage==0||selected.role.equals("boundary");
                check(sample.read==wantRead,"rolepreservesreadwrite");
                int object=(int)((sample.offset-selected.base)/G);
                if(selected.ranks[object]<=(selected.ranks.length+4)/5)hot++;
            }
            for(int member=1;member<=8;member++)
                check(Math.abs(members[member]/(double)scientific-.125)<.02,"uniformmember exposure");
            check(Math.abs(hot/50000.-target/total)<.02,"perfileZipf0.99 target preserved");
        }
        check(tableReads>50,"sharedtables retainallmembers startupreadshare");
        check(!timeline.next(600000000L,sample),"single600secondstop");
        System.out.println("WRF_ENSEMBLE_TESTS_PASS members8 sharedtables4phases exclusiveobjects Zipf0.99 no_physical_io=true");
    }
}
