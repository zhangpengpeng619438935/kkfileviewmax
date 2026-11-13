import com.aspose.cad.Image;
import com.aspose.cad.imageoptions.CadRasterizationOptions;
import com.aspose.cad.imageoptions.PngOptions;
import com.aspose.cad.imageoptions.PdfOptions;

/**
 * 测 DWG 出图速度：同一份图纸分别用「矢量 PDF」和「光栅 PNG」输出，比较耗时。
 * 用法: java -cp .:aspose-cad-23.9.jar CadSpeed <in.dwg> <out> [width] [png|pdf]
 */
public class CadSpeed {
    public static void main(String[] a) throws Exception {
        String in = a[0], out = a[1];
        int w = a.length > 2 ? Integer.parseInt(a[2]) : 1600;
        String mode = a.length > 3 ? a[3] : "png";

        long t0 = System.currentTimeMillis();
        Image img = Image.load(in);
        long t1 = System.currentTimeMillis();

        CadRasterizationOptions ro = new CadRasterizationOptions();
        ro.setAutomaticLayoutsScaling(true);
        ro.setPageWidth(w);
        double ratio = 0.3;
        try {
            ratio = (double) img.getHeight() / (double) img.getWidth();
        } catch (Throwable ignore) { }
        ro.setPageHeight((float) (w * ratio));

        long t2 = System.currentTimeMillis();
        if ("pdf".equals(mode)) {
            PdfOptions po = new PdfOptions();
            po.setVectorRasterizationOptions(ro);
            img.save(out, po);
        } else {
            PngOptions po = new PngOptions();
            po.setVectorRasterizationOptions(ro);
            img.save(out, po);
        }
        long t3 = System.currentTimeMillis();
        System.out.println("MODE=" + mode + " load=" + (t1 - t0) + "ms render+save=" + (t3 - t2)
                + "ms total=" + (t3 - t0) + "ms page=" + w + "x" + (int) (w * ratio));
    }
}
