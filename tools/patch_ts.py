import sys
def rvarb(d,i):
    v=d[i];i+=1
    if not(v&0x80):return v,i
    val=(v&0x7f)<<7;v=d[i];i+=1
    if not(v&0x80):return val|v,i
    val=(((v&0x7f)|val)<<7);v=d[i];i+=1
    if not(v&0x80):return val|v,i
    val=(((v&0x7f)|val)<<7)|d[i];i+=1;return val,i
class BR:
    def __init__(s,d,byte):s.d=d;s.start=byte;s.bp=0
    def pop(s):
        b=s.d[s.start+(s.bp>>3)];bit=(b>>(7-(s.bp&7)))&1;s.bp+=1;return bit
    def popn(s,n):
        v=0
        for _ in range(n):v=(v<<1)|s.pop()
        return v
    def poplen(s):
        n=1
        while s.pop()==0:n+=1
        return n
    def after(s):return s.start+((s.bp+7)>>3)
def parse(d, do_patch):
    n=len(d);pos=0;size=None;vidx=None;fc=0;even=None;patches=[];frames=0
    while pos+2<=n:
        sync=(d[pos]==0x4C and d[pos+1]==0x32)
        if sync:
            size=int.from_bytes(d[pos+12:pos+14],'big')+1
            i=pos+14
            while True:
                typ,i=rvarb(d,i);ssz,i=rvarb(d,i)
                if typ==0:break
                if typ in(1,3):
                    si=d[i]
                    if vidx is None:vidx=si
                    i+=10+(3 if typ==3 else 2)
                elif typ==2:i+=6
                elif typ==4:i+=2
                else:return None
            flags=d[i];i+=1
            if flags&2:i+=2
            ci=i
        else:
            flags=d[pos];i=pos+1
            if flags&2:i+=2
            ci=i
        if size is None:return None
        end=pos+size
        while ci<end and d[ci]!=0:
            br=BR(d,ci)
            bits=br.poplen();si=br.popn(bits);endframe=br.pop()
            ef=None
            if endframe:
                b=br.poplen();br.popn(b);br.pop();b2=br.poplen()
                efbit=ci*8+br.bp;efw=b2*2+26;efv=br.popn(efw)
                ef=(efbit,efw,efv)
            pkt=br.popn(13)+1
            ci=br.after()+pkt
            if endframe and si==vidx:
                if fc%2==0:even=ef[2]
                else:patches.append((ef[0],ef[1],even))
                fc+=1;frames+=1
        pos+=size
    return frames,vidx,patches
d=bytearray(open(sys.argv[1],'rb').read())
frames,vidx,patches=parse(d,False)
print(f"video stream idx={vidx}  frames parsed={frames}  odd-frames-to-patch={len(patches)}")
if len(sys.argv)>2:
    for (bit,w,val) in patches:
        # write val (w bits) MSB-first starting at absolute bit 'bit'
        for k in range(w):
            b=(val>>(w-1-k))&1
            bytei=(bit+k)>>3;shift=7-((bit+k)&7)
            d[bytei]=(d[bytei]&~(1<<shift))|(b<<shift)
    open(sys.argv[2],'wb').write(d)
    print(f"  patched {len(patches)} odd frames -> {sys.argv[2]}")
