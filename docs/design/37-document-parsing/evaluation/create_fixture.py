from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Table,TableStyle,Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from pathlib import Path
import fitz,json,os
base=Path(__file__).parent
pdfmetrics.registerFont(TTFont('ArialU',os.environ.get('PDF_TEST_FONT', '/System/Library/Fonts/Supplemental/Arial Unicode.ttf')))
style=ParagraphStyle('cell',fontName='ArialU',fontSize=10,leading=14)
def P(s):return Paragraph(s,style)
data=[[P('指标 / Metric'),P('2025 财年'),'',P('2026 财年'), ''],['',P('金额（万元）'),P('比例'),P('金额（万元）'),P('比例')],[P('营业收入 / Revenue'),P('1,234,567.89'),P('12.50%'),P('1,345,678.90'),P('13.05%')],[P('净利润 / Net profit'),P('(12,345.67)'),P('-0.80%'),P('23,456.78'),P('1.75%')],[P('坏账率 / Bad debt'),P('0.003'),P('3.00‰'),P('0.0025'),P('2.50‰')],[P('跨行金额 / Wrapped'),P('1,234,<br/>567.89'),P('12.50<br/>%'),P('9,876.<br/>54'),P('0.25‰')],[P('缺失和零 / Missing'),P('—'),P('0.00%'),P('0.00'),P('N/A')]]
c=canvas.Canvas(str(base/'fixture-native.pdf'),pagesize=(595,842))
c.setFont('ArialU',9);c.drawString(42,815,'合成研究报告 | Synthetic research report | NOT REAL FINANCIAL DATA')
c.setFont('ArialU',18);c.drawString(42,775,'复杂表格与数字精度测试')
c.setFont('ArialU',10);c.drawString(42,750,'注意单位、合并表头、负数、千分位、百分号和千分号。')
t=Table(data,colWidths=[155,88,60,88,60],rowHeights=[28,28,42,42,42,48,42]);t.setStyle(TableStyle([('SPAN',(0,0),(0,1)),('SPAN',(1,0),(2,0)),('SPAN',(3,0),(4,0)),('GRID',(0,0),(-1,-1),.5,colors.HexColor('#777777')),('BACKGROUND',(0,0),(-1,1),colors.HexColor('#e9edf3')),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
t.wrap(510,500);t.drawOn(c,42,405)
c.setFont('ArialU',10);c.drawString(42,375,'图 1：示意折线图（单位：%）')
c.line(70,150,70,340);c.line(70,150,520,150)
points=[(100,190,'1.25%'),(220,260,'2.50%'),(340,225,'1.90%'),(460,310,'3.75%')]
for i,(x,y,label) in enumerate(points):
 c.circle(x,y,3,fill=1);c.drawString(x-15,y+14,label);c.drawString(x-8,130,f'Q{i+1}')
 if i:c.line(points[i-1][0],points[i-1][1],x,y)
c.setFont('ArialU',8);c.drawString(42,85,'注：括号表示负数；— 为缺失值；‰ 不等于 %。表内换行不可造成列错位。')
c.drawString(42,38,'页脚：合成数据，仅用于解析测试。');c.drawRightString(550,38,'1 / 2');c.save()
doc=fitz.open(base/'fixture-native.pdf');pix=doc[0].get_pixmap(matrix=fitz.Matrix(2,2));pix.save(base/'fixture-page.png')
# Same content as an image-only second page, so OCR cannot rely on PDF text layer.
p=doc.new_page(width=595,height=842);p.insert_image(p.rect,filename=str(base/'fixture-page.png'));doc.save(base/'complex-numbers-two-pages.pdf')
(base/'ground-truth.json').write_text(json.dumps({'description':'page1 native text; page2 raster copy of page1 (repeated printed footer intentional)','values':['1,234,567.89','12.50%','1,345,678.90','13.05%','(12,345.67)','-0.80%','23,456.78','1.75%','0.003','3.00‰','0.0025','2.50‰','9,876.54','0.25‰','0.00%','0.00','N/A'],'wrapped':['1,234, / 567.89','12.50 / %','9,876. / 54']},ensure_ascii=False,indent=2))
