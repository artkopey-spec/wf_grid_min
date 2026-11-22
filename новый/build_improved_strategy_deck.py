from math import ceil
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.dml import MSO_LINE_DASH_STYLE


OUT = "C:/3.1_wf_grid/MIX_ST_19_4_strategy_improved.pptx"
SRC = "C:/3.1_wf_grid/MIX_ST_19_4_final_strategy_source.pptx"

W, H = 13.333, 7.5
BG = "07141E"
PANEL = "0D2230"
PANEL_2 = "102A39"
GRID = "24404E"
TEXT = "F4F8FA"
MUTED = "9EB2BE"
TEAL = "20D3C2"
GREEN = "89E36B"
ORANGE = "FFB45B"
RED = "FF6B6B"
BLUE = "65A7FF"
WHITE = "FFFFFF"


def rgb(hex_color):
    return RGBColor.from_string(hex_color.replace("#", ""))


def add_rect(slide, x, y, w, h, fill, radius=True, line=None, transparency=0):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = rgb(fill)
    sh.fill.transparency = transparency
    sh.line.color.rgb = rgb(line or fill)
    if radius:
        try:
            sh.adjustments[0] = 0.08
        except Exception:
            pass
    return sh


def add_text(slide, text, x, y, w, h, size=18, color=TEXT, bold=False,
             align=PP_ALIGN.LEFT, font="Aptos", valign=MSO_ANCHOR.TOP,
             margin=0.02, italic=False, rotation=0):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.rotation = rotation
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_after = Pt(0)
    p.space_before = Pt(0)
    p.line_spacing = 1.0
    r = p.add_run()
    r.text = text
    r.font.name = font
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = rgb(color)
    return box


def rich_text(slide, runs, x, y, w, h, size=18, align=PP_ALIGN.LEFT,
              valign=MSO_ANCHOR.TOP, margin=0.02):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_after = Pt(0)
    p.line_spacing = 1.0
    for spec in runs:
        r = p.add_run()
        r.text = spec[0]
        r.font.name = "Aptos"
        r.font.size = Pt(spec[1] if len(spec) > 1 else size)
        r.font.bold = spec[2] if len(spec) > 2 else False
        r.font.color.rgb = rgb(spec[3] if len(spec) > 3 else TEXT)
    return box


def add_line(slide, x1, y1, x2, y2, color=GRID, width=1.5, dash=None):
    sh = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    sh.line.color.rgb = rgb(color)
    sh.line.width = Pt(width)
    if dash:
        sh.line.dash_style = dash
    return sh


def add_circle(slide, cx, cy, d, fill, text=None, text_color=BG, size=14):
    sh = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(cx-d/2), Inches(cy-d/2), Inches(d), Inches(d))
    sh.fill.solid(); sh.fill.fore_color.rgb = rgb(fill)
    sh.line.color.rgb = rgb(fill)
    if text is not None:
        add_text(slide, str(text), cx-d/2, cy-d/2+0.005, d, d-0.01, size, text_color, True,
                 PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
    return sh


def base_slide(prs, title, subtitle, number):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid(); bg.fore_color.rgb = rgb(BG)
    add_rect(slide, 0, 0, 0.12, H, TEAL, False)
    add_text(slide, title, 0.68, 0.28, 10.7, 0.52, 26, TEXT, True)
    add_text(slide, "MIX · ST 19/4", 11.0, 0.34, 1.65, 0.25, 10, MUTED, True, PP_ALIGN.RIGHT)
    add_line(slide, 0.68, 0.94, 12.62, 0.94, GRID, 1)
    add_text(slide, subtitle, 0.70, 1.03, 11.9, 0.34, 12, MUTED)
    add_text(slide, "MIX · минутные данные · 21.08.2023–21.08.2026", 0.70, 7.17, 7.5, 0.18, 8, MUTED)
    add_text(slide, f"{number:02d}", 12.12, 7.13, 0.48, 0.22, 9, MUTED, True, PP_ALIGN.RIGHT)
    return slide


def metric_card(slide, x, y, w, value, label, accent=TEAL, value_size=24):
    add_rect(slide, x, y, w, 1.05, PANEL, True, GRID)
    add_rect(slide, x, y, 0.06, 1.05, accent, False)
    add_text(slide, value, x+0.22, y+0.15, w-0.35, 0.39, value_size, TEXT, True)
    add_text(slide, label, x+0.22, y+0.62, w-0.35, 0.22, 10, MUTED)


def bullet(slide, x, y, w, title, body, color=TEAL, n=None):
    if n is None:
        add_circle(slide, x+0.15, y+0.16, 0.11, color)
        tx = x+0.34
    else:
        add_circle(slide, x+0.20, y+0.23, 0.36, color, n, BG, 12)
        tx = x+0.52
    add_text(slide, title, tx, y, w-(tx-x), 0.28, 14, TEXT, True)
    add_text(slide, body, tx, y+0.33, w-(tx-x), 0.55, 10.5, MUTED)


def extract_equity():
    src = Presentation(SRC)
    s = src.slides[3]
    segs = []
    for sh in s.shapes:
        if sh.shape_type == 9 and sh.left/Inches(1) >= 1.4 and sh.left/Inches(1) <= 8.7:
            x = sh.left / Inches(1)
            y = sh.top / Inches(1)
            w = sh.width / Inches(1)
            h = sh.height / Inches(1)
            flip_v = sh._element.spPr.xfrm.get("flipV") in ("1", "true")
            y0, y1 = (y+h, y) if flip_v else (y, y+h)
            segs.append((x, y0, x+w, y1))
    segs.sort(key=lambda z: z[0])
    pts = []
    if segs:
        pts.append((segs[0][0], segs[0][1]))
        pts.extend((z[2], z[3]) for z in segs)
    # Convert the original axis: y=5.85 -> 0m; y=3.78 -> 25m.
    values = [(5.85-y) * 25.0 / (5.85-3.78) for _, y in pts]
    return values


prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
prs.core_properties.title = "MIX · SuperTrend 19/4 — торговая стратегия"
prs.core_properties.subject = "Улучшенная визуальная версия презентации стратегии"
prs.core_properties.author = "OpenAI Codex"
prs.core_properties.comments = "Финальная версия: подтверждённое усиление 100+100, контроль 30 минут и аварийный стоп 2%."

# 1 — cover
s = prs.slides.add_slide(prs.slide_layouts[6])
s.background.fill.solid(); s.background.fill.fore_color.rgb = rgb(BG)
add_rect(s, 0, 0, 0.16, H, TEAL, False)
add_text(s, "ТОРГОВАЯ СТРАТЕГИЯ", 0.78, 0.73, 5.5, 0.30, 12, TEAL, True)
add_text(s, "MIX", 0.76, 1.15, 3.0, 0.92, 52, TEXT, True)
add_text(s, "SuperTrend 19 / 4", 0.80, 2.04, 5.7, 0.48, 26, MUTED, False)
add_text(s, "Коррекция → подтверждённый пробой\n→ контролируемое усиление", 0.80, 2.80, 6.4, 1.05, 25, TEXT, True)

# abstract price path
panel = add_rect(s, 7.45, 0.65, 5.15, 5.75, PANEL, True, GRID)
pts = [(7.92,5.20),(8.45,4.78),(8.96,4.98),(9.46,4.12),(9.96,3.18),(10.42,3.62),(10.88,4.20),(11.35,3.55),(12.08,3.24)]
for i in range(len(pts)-1):
    c = GREEN if i < 4 else (RED if i < 6 else TEAL)
    add_line(s, *pts[i], *pts[i+1], c, 4)
add_line(s, 10.0, 3.18, 10.88, 4.20, RED, 1.2, MSO_LINE_DASH_STYLE.DASH)
add_circle(s, 10.88, 4.20, 0.18, TEAL)
add_text(s, "−0,4% · LONG", 10.15, 4.48, 1.65, 0.25, 11, TEAL, True, PP_ALIGN.CENTER)
add_text(s, ">0,7%", 9.03, 2.68, 1.55, 0.36, 20, GREEN, True, PP_ALIGN.CENTER)
add_text(s, "импульс", 9.24, 3.00, 1.1, 0.22, 9, MUTED, False, PP_ALIGN.CENTER)
add_text(s, "СЛЕД. ОТКРЫТИЕ", 11.13, 2.77, 1.18, 0.22, 8, MUTED, True, PP_ALIGN.CENTER)

metric_card(s, 0.80, 5.25, 2.18, "+31,25", "млн ₽ · Σ +111,00%", GREEN, 21)
metric_card(s, 3.15, 5.25, 1.85, "2,41", "Profit Factor", TEAL, 23)
metric_card(s, 5.17, 5.25, 1.85, "−12,67%", "max DD · −2,79 млн ₽", BLUE, 19)
add_text(s, "Бэктест · 100→200 MIX · комиссия включена", 0.82, 6.66, 6.6, 0.24, 10, MUTED)
add_text(s, "01", 12.10, 7.10, 0.45, 0.22, 9, MUTED, True, PP_ALIGN.RIGHT)

# 2 — strategy at a glance
s = base_slide(prs, "Стратегия на одном слайде", "Первый вход на коррекции, усиление только после подтверждения, риск ограничен временем и стопом", 2)
stages = [
    ("1", "ИМПУЛЬС", "Первый зелёный ST дня\nстрого > 0,7%", GREEN),
    ("2", "СМЕНА РЕЖИМА", "ST становится красным;\nфиксируем вершину", ORANGE),
    ("3", "ВХОД", "Long 100 MIX\nна −0,4% от вершины", TEAL),
    ("4", "УСИЛЕНИЕ", "Close > вершины;\n+100 по след. Open", BLUE),
    ("5", "РИСК / ВЫХОД", "30 мин. без роста — выход;\nстоп 2%; иначе след. Open", TEXT),
]
for i,(n,t,b,c) in enumerate(stages):
    x=0.72+i*2.45
    add_rect(s,x,1.60,2.16,2.10,PANEL,True,GRID)
    add_circle(s,x+0.33,1.94,0.38,c,n,BG,12)
    add_text(s,t,x+0.62,1.79,1.33,0.35,11,c,True)
    add_text(s,b,x+0.24,2.43,1.72,0.78,13,TEXT,True)
    if i<4:
        add_text(s,"→",x+2.18,2.38,0.27,0.35,18,MUTED,True,PP_ALIGN.CENTER)
add_rect(s,0.72,4.05,7.65,1.80,PANEL_2,True,GRID)
add_text(s,"КЛЮЧЕВАЯ ИДЕЯ",1.02,4.32,2.4,0.25,11,TEAL,True)
add_text(s,"Покупаем не продолжение импульса,\nа скидку внутри ещё сильного сценария.",1.02,4.78,6.85,0.66,22,TEXT,True)
add_rect(s,8.63,4.05,3.98,1.80,PANEL,True,GRID)
add_text(s,"СДЕЛКИ НЕТ, ЕСЛИ",8.95,4.32,2.8,0.25,11,ORANGE,True)
add_text(s,"уровень −0,4% не достигнут\nдо окончания красного ST",8.95,4.78,3.10,0.66,15,TEXT,True)
add_text(s,"Усиление выполняется только по Open минуты после подтверждающего Close — вход по High запрещён.",0.78,6.25,10.8,0.30,11,MUTED,False,italic=True)

# 3 — trade anatomy
s = base_slide(prs, "Анатомия сделки", "Вход привязан к цене, выход — ко времени: правила легко проверить и автоматизировать", 3)
add_rect(s,0.72,1.55,11.90,3.70,PANEL,True,GRID)
# grid
for y in [2.25,3.05,3.85,4.65]:
    add_line(s,1.08,y,12.25,y,GRID,0.7,MSO_LINE_DASH_STYLE.DASH)
path=[(1.06,4.55),(1.75,4.18),(2.40,4.34),(3.05,3.63),(3.65,2.92),(4.25,2.26),(4.85,2.50),(5.40,3.15),(6.05,3.78),(6.55,3.30),(7.15,3.55),(7.72,3.08),(8.35,2.85),(9.35,2.83),(10.45,2.82),(11.82,2.80)]
for i in range(len(path)-1):
    if i<5: c=GREEN
    elif i<8: c=RED
    else: c=TEAL
    add_line(s,*path[i],*path[i+1],c,3.6)
add_line(s,4.25,2.26,6.05,3.78,RED,1.0,MSO_LINE_DASH_STYLE.DASH)
add_circle(s,4.25,2.26,0.16,GREEN)
add_circle(s,6.05,3.78,0.20,TEAL)
add_circle(s,11.82,2.80,0.18,TEXT)
add_text(s,"ВЕРШИНА",3.74,1.83,1.05,0.22,9,GREEN,True,PP_ALIGN.CENTER)
add_text(s,"−0,4% · LONG",5.28,4.02,1.60,0.24,10,TEAL,True,PP_ALIGN.CENTER)
add_text(s,"СЛЕДУЮЩЕЕ ОТКРЫТИЕ",10.58,2.35,1.65,0.25,8,TEXT,True,PP_ALIGN.CENTER)
add_text(s,"красный ST",4.96,4.63,1.55,0.22,9,RED,True,PP_ALIGN.CENTER)
add_line(s,4.60,4.45,7.30,4.45,RED,2.2)
for i,(t,c) in enumerate([("Импульс >0,7%",GREEN),("Коррекция",RED),("Long",TEAL),("Временной выход",TEXT)]):
    x=0.86+i*3.02
    add_circle(s,x+0.08,5.73,0.11,c)
    add_text(s,t,x+0.22,5.61,2.40,0.25,11,TEXT,True)
add_rect(s,0.72,6.18,11.90,0.62,PANEL_2,True,GRID)
add_text(s,"Контроль качества сигнала:",1.00,6.35,2.35,0.24,11,TEAL,True)
add_text(s,"только первый сильный импульс дня · только во время красного ST · без опоздавшего входа",3.20,6.35,8.92,0.24,11,TEXT)

# 4 — edge hypothesis & funnel
s = base_slide(prs, "Откуда может браться преимущество", "Это объясняющая гипотеза, а не доказательство причинности", 4)
ideas=[
    ("СИЛЬНЫЙ ИМПУЛЬС",">0,7% отделяет обычный шум от редкого направленного движения.",GREEN,"01"),
    ("ЛУЧШАЯ ЦЕНА", "Вход на откате −0,4% не платит максимум за подтверждение.",TEAL,"02"),
    ("ВРЕМЕННОЙ КАТАЛИЗАТОР", "Следующее открытие стандартизирует горизонт и убирает субъективность.",BLUE,"03"),
]
for i,(t,b,c,n) in enumerate(ideas):
    y=1.60+i*1.42
    add_rect(s,0.72,y,7.15,1.15,PANEL,True,GRID)
    add_text(s,n,1.00,y+0.18,0.62,0.35,18,c,True)
    add_text(s,t,1.78,y+0.16,2.80,0.28,12,c,True)
    add_text(s,b,4.43,y+0.13,3.05,0.68,11,TEXT)

add_rect(s,8.15,1.60,4.47,4.00,PANEL_2,True,GRID)
add_text(s,"ВОРОНКА БЭКТЕСТА",8.52,1.92,3.55,0.28,11,MUTED,True)
funnel=[("326","сигналов",4.00,ORANGE),("271","исполнено",3.25,TEAL),("≈162","прибыльных",2.50,GREEN)]
for i,(v,l,w,c) in enumerate(funnel):
    x=10.385-w/2; y=2.50+i*0.88
    add_rect(s,x,y,w,0.67,c,True,c)
    add_text(s,v,x+0.15,y+0.10,0.85,0.30,18,BG,True)
    add_text(s,l,x+1.02,y+0.15,w-1.18,0.24,10,BG,True,PP_ALIGN.RIGHT)
add_text(s,"83,1% fill rate",8.60,5.04,3.55,0.27,12,TEAL,True,PP_ALIGN.CENTER)
add_rect(s,0.72,6.10,11.90,0.66,PANEL,True,GRID)
add_text(s,"Что важно проверить:",1.00,6.29,2.20,0.24,11,ORANGE,True)
add_text(s,"устойчивость edge вне выборки, по годам и при реалистичном проскальзывании",3.05,6.29,8.95,0.24,11,TEXT)

# 5 — parameters
s = base_slide(prs, "Почему 0,7% и −0,4%", "Оба параметра работают как фильтры качества; максимум лежит в середине протестированного диапазона", 5)
add_rect(s,0.72,1.55,5.75,4.85,PANEL,True,GRID)
add_text(s,"ПОРОГ ИМПУЛЬСА",1.03,1.85,3.2,0.28,12,MUTED,True)
imp=[(">0,6%",13.44,52.2,MUTED),(">0,7%",20.40,77.5,GREEN)]
for i,(lab,val,pct,c) in enumerate(imp):
    y=2.45+i*1.05
    add_text(s,lab,1.04,y,0.80,0.30,13,TEXT,True)
    add_rect(s,1.88,y+0.04,3.05,0.26,GRID,True,GRID)
    add_rect(s,1.88,y+0.04,3.05*val/20.4,0.26,c,True,c)
    add_text(s,f"+{val:.2f} млн ₽",5.02,y-0.02,1.10,0.28,11,c,True,PP_ALIGN.RIGHT)
add_rect(s,1.03,4.80,5.12,1.05,PANEL_2,True,GRID)
add_text(s,"+52%",1.30,5.08,1.10,0.36,22,GREEN,True)
add_text(s,"больше net против порога 0,6%",2.35,5.12,3.15,0.28,11,TEXT)

add_rect(s,6.75,1.55,5.87,4.85,PANEL,True,GRID)
add_text(s,"КОРРЕКЦИЯ ДЛЯ ВХОДА",7.06,1.85,3.6,0.28,12,MUTED,True)
corr=[("−0,3%",10.68,TEAL),("−0,4%",13.44,GREEN),("−0,5%",3.91,ORANGE)]
for i,(lab,val,c) in enumerate(corr):
    y=2.39+i*0.84
    add_text(s,lab,7.06,y,0.82,0.30,13,TEXT,True)
    add_rect(s,7.94,y+0.04,2.85,0.24,GRID,True,GRID)
    add_rect(s,7.94,y+0.04,2.85*val/13.44,0.24,c,True,c)
    add_text(s,f"+{val:.2f}",10.95,y-0.02,1.18,0.28,11,c,True,PP_ALIGN.RIGHT)
add_text(s,"−0,3%",7.08,5.15,0.74,0.25,11,TEAL,True)
add_text(s,"слишком рано",7.82,5.15,1.22,0.25,10,MUTED)
add_text(s,"−0,5%",9.15,5.15,0.74,0.25,11,ORANGE,True)
add_text(s,"уже слабый сценарий",9.89,5.15,2.12,0.25,10,MUTED)
add_text(s,"Вывод: параметры выглядят осмысленно, но соседних точек пока мало для оценки ширины устойчивого плато.",0.78,6.62,11.4,0.28,11,ORANGE,False,italic=True)

# 6 — results & equity
s = base_slide(prs, "Результат бэктеста", "Комиссия включена · 11 межконтрактных переходов скорректированы", 6)
cards=[("+20,40 млн ₽","net · Σ +77,51%",GREEN),("59,8%","прибыльных",TEAL),("1,64","Profit Factor",BLUE),("−3,97 млн ₽","max DD · Σ −12,83%",RED)]
for i,(v,l,c) in enumerate(cards): metric_card(s,0.72+i*3.02,1.52,2.78,v,l,c,20)
add_rect(s,0.72,2.86,8.55,3.72,PANEL,True,GRID)
add_text(s,"НАКОПЛЕННЫЙ P&L · МЛН ₽",1.02,3.13,4.0,0.25,11,MUTED,True)
values=extract_equity()
plot=(1.22,3.68,7.55,2.35)
x0,y0,pw,ph=plot
for val in [0,5,10,15,20,25]:
    yy=y0+ph-(val/25)*ph
    add_line(s,x0,yy,x0+pw,yy,GRID,0.6)
    add_text(s,str(val),0.89,yy-0.10,0.26,0.18,8,MUTED,False,PP_ALIGN.RIGHT)
if values:
    lo,hi=0,max(25,max(values))
    for i in range(len(values)-1):
        xa=x0+pw*i/(len(values)-1); xb=x0+pw*(i+1)/(len(values)-1)
        ya=y0+ph-(values[i]/hi)*ph; yb=y0+ph-(values[i+1]/hi)*ph
        add_line(s,xa,ya,xb,yb,TEAL,2.1)
    add_circle(s,x0+pw,y0+ph-(values[-1]/hi)*ph,0.16,GREEN)
add_text(s,"1",1.14,6.10,0.32,0.20,8,MUTED)
add_text(s,"271",8.42,6.10,0.36,0.20,8,MUTED,False,PP_ALIGN.RIGHT)
add_rect(s,9.52,2.86,3.10,3.72,PANEL_2,True,GRID)
add_text(s,"ВЫБОРКА",9.84,3.14,1.50,0.24,11,MUTED,True)
add_text(s,"630 299",9.84,3.64,2.28,0.40,24,TEXT,True)
add_text(s,"минутных баров",9.84,4.07,2.28,0.24,10,MUTED)
add_line(s,9.84,4.55,12.26,4.55,GRID,1)
add_text(s,"326",9.84,4.86,0.82,0.35,19,ORANGE,True)
add_text(s,"сигналов",10.74,4.91,1.20,0.24,10,MUTED)
add_text(s,"271",9.84,5.40,0.82,0.35,19,TEAL,True)
add_text(s,"сделка",10.74,5.45,1.20,0.24,10,MUTED)
add_text(s,"83,1% исполнено",9.84,6.08,2.24,0.25,11,TEXT,True)

# 7 — economics
s = base_slide(prs, "Экономика одной сделки", "Средние выигрыш и число исходов — расчётная оценка из win rate, PF и среднего убытка", 7)
add_rect(s,0.72,1.55,7.00,4.85,PANEL,True,GRID)
add_text(s,"≈162",1.05,1.98,1.52,0.54,30,GREEN,True)
add_text(s,"прибыльных",1.05,2.48,1.52,0.24,10,MUTED)
add_text(s,"≈109",5.89,1.98,1.40,0.54,30,RED,True,PP_ALIGN.RIGHT)
add_text(s,"убыточных",5.62,2.48,1.67,0.24,10,MUTED,False,PP_ALIGN.RIGHT)
add_line(s,1.20,3.30,7.20,3.30,GRID,8)
add_line(s,1.20,3.30,1.20+6.0*0.598,3.30,GREEN,8)
add_circle(s,1.20+6.0*0.598,3.30,0.20,TEXT)
add_text(s,"59,8%",3.18,2.86,1.45,0.30,15,TEXT,True,PP_ALIGN.CENTER)
add_rect(s,1.02,3.90,2.72,1.56,PANEL_2,True,GRID)
add_text(s,"≈ +323 тыс. ₽",1.28,4.18,2.20,0.36,20,GREEN,True)
add_text(s,"средняя прибыль · оценка",1.28,4.73,2.15,0.24,10,MUTED)
add_rect(s,4.02,3.90,2.72,1.56,PANEL_2,True,GRID)
add_text(s,"−293 тыс. ₽",4.28,4.18,2.20,0.36,20,RED,True)
add_text(s,"средний убыток · факт",4.28,4.73,2.15,0.24,10,MUTED)
add_text(s,"Соотношение ≈ 1,10 : 1",2.24,5.83,3.90,0.28,12,TEAL,True,PP_ALIGN.CENTER)

add_rect(s,7.98,1.55,4.64,4.85,PANEL_2,True,GRID)
add_text(s,"ОЖИДАЕМАЯ СДЕЛКА",8.35,1.92,3.50,0.25,11,MUTED,True)
add_text(s,"+75 264 ₽",8.35,2.50,3.50,0.62,34,GREEN,True)
add_text(s,"+0,286% к номиналу входа",8.35,3.16,3.50,0.28,12,TEXT)
add_line(s,8.35,3.76,12.20,3.76,GRID,1)
add_text(s,"Почему стратегия положительна",8.35,4.10,3.50,0.25,12,TEAL,True)
add_text(s,"Win rate выше 50%, а средний выигрыш немного больше среднего проигрыша — оба фактора работают в одну сторону.",8.35,4.54,3.48,1.16,12,TEXT)
add_text(s,"≈ — производная оценка, возможны округления.",8.35,5.93,3.50,0.20,8,MUTED,False,italic=True)

# 8 — loss anatomy
s = base_slide(prs, "Главный риск — продолжение падения", "Типичный проигрыш формируется внутри позиции, а не только гэпом следующего открытия", 8)
metric_card(s,0.72,1.52,2.80,"−203 тыс. ₽","медианный убыток · −0,698%",RED,20)
metric_card(s,3.69,1.52,2.80,"−293 тыс. ₽","средний убыток · −1,050%",RED,20)
metric_card(s,6.66,1.52,2.80,"−1,24%","типичный max adverse move",ORANGE,20)
metric_card(s,9.63,1.52,2.99,"7 часов","медиана до минимума",BLUE,20)
add_rect(s,0.72,2.92,7.38,3.48,PANEL,True,GRID)
add_text(s,"ГЕОМЕТРИЯ ПРОИГРЫША",1.02,3.20,3.2,0.26,11,MUTED,True)
loss=[("Вход","−0,4% от вершины",TEAL),("Продолжение вниз","ещё ≈ −1,24%",RED),("Неполный отскок","часть падения возвращена",ORANGE),("След. открытие","≈ −0,69% от входа",TEXT)]
for i,(t,b,c) in enumerate(loss):
    y=3.70+i*0.60
    add_circle(s,1.17,y+0.16,0.28,c,i+1,BG,10)
    add_text(s,t,1.46,y,2.28,0.25,12,TEXT,True)
    add_text(s,b,4.05,y,3.45,0.25,11,c,True,PP_ALIGN.RIGHT)
    if i<3: add_line(s,1.17,y+0.31,1.17,y+0.62,GRID,1.2)
add_rect(s,8.35,2.92,4.27,3.48,PANEL_2,True,GRID)
add_text(s,"90,8%",8.72,3.35,1.55,0.50,29,RED,True)
add_text(s,"убытков уже были ниже входа\nперед следующим открытием",10.05,3.36,2.07,0.72,11,TEXT)
add_line(s,8.72,4.38,12.18,4.38,GRID,1)
add_text(s,"−89…−381 тыс. ₽",8.72,4.72,3.10,0.34,18,TEXT,True)
add_text(s,"центральные 50% убытков",8.72,5.10,3.10,0.22,10,MUTED)
add_text(s,"Худшая: −2,13 млн ₽ · −7,145%",8.72,5.72,3.18,0.28,11,RED,True)
add_text(s,"Следствие: риск-контроль должен реагировать на затянувшийся минус, а не на первый шумовой прокол.",0.78,6.67,11.65,0.25,11,ORANGE,False,italic=True)

# 9 — risk variants
s = base_slide(prs, "Контроль через 6 часов — практический компромисс", "Он отдаёт 8,5% net, но заметно сокращает просадку и худшую сделку", 9)
variants=[
    ("База",20.40,3.97,2.13,1.64,GREEN),
    ("6 ч.: выйти, если минус",18.66,2.86,1.05,1.66,TEAL),
    ("Стоп −1,5%",15.22,2.25,1.36,1.48,ORANGE),
    ("Touch + контроль",6.45,0.91,0.47,2.03,BLUE),
]
add_text(s,"NET · МЛН ₽",3.80,1.57,1.55,0.25,10,MUTED,True)
add_text(s,"MAX DD",8.10,1.57,1.15,0.25,10,MUTED,True)
add_text(s,"ХУДШАЯ",9.73,1.57,1.20,0.25,10,MUTED,True)
add_text(s,"PF",11.48,1.57,0.55,0.25,10,MUTED,True)
for i,(name,net,dd,worst,pf,c) in enumerate(variants):
    y=2.03+i*0.82
    if i==1: add_rect(s,0.72,y-0.12,11.90,0.68,PANEL_2,True,TEAL)
    add_text(s,name,0.95,y,2.55,0.27,12,TEXT,True)
    add_rect(s,3.80,y+0.03,3.65,0.22,GRID,True,GRID)
    add_rect(s,3.80,y+0.03,3.65*net/20.4,0.22,c,True,c)
    add_text(s,f"{net:.2f}",7.56,y-0.01,0.70,0.27,11,c,True,PP_ALIGN.RIGHT)
    add_text(s,f"−{dd:.2f}",8.40,y-0.01,0.77,0.27,11,TEXT,True,PP_ALIGN.RIGHT)
    add_text(s,f"−{worst:.2f}",9.96,y-0.01,0.86,0.27,11,TEXT,True,PP_ALIGN.RIGHT)
    add_text(s,f"{pf:.2f}",11.45,y-0.01,0.62,0.27,11,c,True,PP_ALIGN.RIGHT)
add_rect(s,0.72,5.62,11.90,0.87,PANEL_2,True,TEAL)
add_text(s,"РЕКОМЕНДАЦИЯ",1.02,5.89,1.72,0.24,11,TEAL,True)
add_text(s,"Через 6 часов: ниже входа — закрыть; выше входа — держать до следующего открытия.",2.80,5.82,8.95,0.38,15,TEXT,True)
add_text(s,"Эффект vs база: net −1,74 млн ₽ · max DD лучше на 1,11 млн ₽ · худшая сделка лучше на 1,08 млн ₽.",0.78,6.67,11.65,0.25,10,MUTED,False,italic=True)

# 10 — rejected alternatives
s = base_slide(prs, "Что не добавило преимущества", "Сложнее не значит лучше: подтверждения и дробление входа снижали результат", 10)
alts=[
    ("Вход сразу после импульса",11.95,"PF 1,24","Платим максимум за подтверждение",ORANGE),
    ("50 на −0,3% + 50 на −0,6%",5.34,"Σ +51,19%","Вторая часть почти не заработала",RED),
    ("Ждать повторного зелёного ST",13.56,"PF 1,36","Запаздывает; DD −4,36 млн ₽",BLUE),
    ("Фильтр красной коррекции 90%",7.32,"Σ +28,52%","Риск ниже, прибыль почти вдвое меньше",TEAL),
]
for i,(name,net,tag,note,c) in enumerate(alts):
    x=0.72+(i%2)*6.05; y=1.57+(i//2)*2.13
    add_rect(s,x,y,5.84,1.80,PANEL,True,GRID)
    add_rect(s,x,y,0.06,1.80,c,False)
    add_text(s,name,x+0.30,y+0.22,4.25,0.35,13,TEXT,True)
    add_text(s,f"+{net:.2f} млн ₽",x+0.30,y+0.78,2.15,0.34,19,c,True)
    add_text(s,tag,x+3.72,y+0.83,1.58,0.25,10,MUTED,True,PP_ALIGN.RIGHT)
    add_text(s,note,x+0.30,y+1.31,4.95,0.27,10,MUTED)
add_rect(s,0.72,5.98,11.90,0.67,PANEL_2,True,GRID)
add_text(s,"ПРИНЦИП",1.02,6.19,1.20,0.22,10,GREEN,True)
add_text(s,"Сохранить простое правило −0,4%: оно даёт лучший баланс цены входа и качества сигнала.",2.20,6.16,9.60,0.28,13,TEXT,True)

# 11 — deployment
s = base_slide(prs, "От бэктеста к реальному запуску", "Алгоритм готов к формализации; доказательность и исполнение ещё требуют проверки", 11)
steps=[
    ("01","ЗАМОРОЗИТЬ ПРАВИЛА","ST 19/4 · >0,7% · −0,4% · 6 ч. · следующее открытие",GREEN),
    ("02","WALK-FORWARD / OOS","Проверить по годам, режимам волатильности и на невидимых данных",TEAL),
    ("03","ИСПОЛНЕНИЕ","Проскальзывание, лимит/market, ликвидность 100 MIX, частичные fill",BLUE),
    ("04","РИСК НА КАПИТАЛ","Размер позиции от допустимого убытка, дневной лимит и kill switch",ORANGE),
]
for i,(n,t,b,c) in enumerate(steps):
    y=1.55+i*1.08
    add_text(s,n,0.80,y+0.14,0.52,0.30,15,c,True)
    add_rect(s,1.48,y,6.62,0.86,PANEL,True,GRID)
    add_text(s,t,1.76,y+0.13,2.36,0.25,11,c,True)
    add_text(s,b,4.05,y+0.10,3.66,0.52,10.5,TEXT)
add_rect(s,8.38,1.55,4.24,4.20,PANEL_2,True,GRID)
add_text(s,"РИСКИ, КОТОРЫЕ НЕЛЬЗЯ\nСЧИТАТЬ ЗАКРЫТЫМИ",8.74,1.88,3.40,0.62,13,ORANGE,True)
risks=["переоптимизация двух порогов","нестабильность по режимам рынка","market impact и реальные fill","Σ% не равна доходности на маржу"]
for i,r in enumerate(risks):
    add_circle(s,8.84,2.91+i*0.58,0.11,ORANGE)
    add_text(s,r,9.05,2.78+i*0.58,2.95,0.30,11,TEXT)
add_rect(s,0.72,6.08,11.90,0.66,TEAL,True,TEAL)
add_text(s,"GO / NO-GO",1.02,6.29,1.38,0.23,11,BG,True)
add_text(s,"Запускать только после положительного OOS и подтверждения исполнения на малом размере.",2.42,6.24,9.30,0.31,14,BG,True)

# 12 — confirmed breakout add-on
s = base_slide(prs, "Усиление позиции после подтверждённого пробоя", "Опциональная вторая покупка: сигнал появляется только после закрытия минутного бара выше вершины импульса", 12)

# Execution flow
flow = [
    ("1", "БАЗОВЫЙ ВХОД", "Long 100 MIX\nна −0,4% от вершины", TEAL),
    ("2", "ПОДТВЕРЖДЕНИЕ", "Close минутного бара\nстрого выше вершины", GREEN),
    ("3", "УСИЛЕНИЕ", "+100 MIX по Open\nследующей минуты", BLUE),
    ("4", "ВЫХОД", "Все 200 MIX\nна следующем открытии", TEXT),
]
for i, (n, title, body, color) in enumerate(flow):
    x = 0.72 + i * 3.02
    add_rect(s, x, 1.52, 2.74, 1.62, PANEL, True, GRID)
    add_circle(s, x + 0.34, 1.87, 0.38, color, n, BG, 12)
    add_text(s, title, x + 0.65, 1.71, 1.78, 0.30, 11, color, True)
    add_text(s, body, x + 0.26, 2.27, 2.20, 0.56, 13, TEXT, True)
    if i < 3:
        add_text(s, "→", x + 2.76, 2.10, 0.26, 0.34, 18, MUTED, True, PP_ALIGN.CENTER)

# Result comparison
add_rect(s, 0.72, 3.48, 7.48, 2.72, PANEL, True, GRID)
add_text(s, "СРАВНЕНИЕ НА ОДНОЙ ВЫБОРКЕ", 1.02, 3.77, 3.50, 0.26, 11, MUTED, True)
headers = [("Вариант", 1.02), ("Net", 4.20), ("PF", 5.82), ("Max DD", 6.63)]
for label, x in headers:
    add_text(s, label, x, 4.21, 1.15, 0.22, 9, MUTED, True)
rows = [
    ("Только 100 на −0,4%", "+20,40 млн ₽", "1,64", "−3,97 млн ₽", MUTED),
    ("+100 после Close > max", "+32,73 млн ₽", "1,71", "−5,89 млн ₽", GREEN),
]
for i, (name, net, pf, dd, color) in enumerate(rows):
    y = 4.57 + i * 0.66
    if i == 1:
        add_rect(s, 0.94, y - 0.10, 6.95, 0.54, PANEL_2, True, GREEN)
    add_text(s, name, 1.03, y, 2.85, 0.25, 11, TEXT, True)
    add_text(s, net, 4.05, y, 1.42, 0.25, 11, color, True, PP_ALIGN.RIGHT)
    add_text(s, pf, 5.80, y, 0.56, 0.25, 11, color, True, PP_ALIGN.RIGHT)
    add_text(s, dd, 6.45, y, 1.26, 0.25, 11, RED if i else TEXT, True, PP_ALIGN.RIGHT)

# Key metrics and implementation warning
add_rect(s, 8.46, 3.48, 4.16, 2.72, PANEL_2, True, GRID)
add_text(s, "РЕЗУЛЬТАТ УСИЛЕНИЯ", 8.78, 3.77, 3.10, 0.25, 11, MUTED, True)
add_text(s, "+32,73 млн ₽", 8.78, 4.22, 3.18, 0.42, 25, GREEN, True)
add_text(s, "Σ +122,76% к номиналу первых 100", 8.78, 4.70, 3.20, 0.25, 11, TEXT, True)
add_text(s, "156 из 271", 8.78, 5.16, 1.38, 0.30, 17, BLUE, True)
add_text(s, "сделок получили вторую часть", 10.18, 5.20, 1.80, 0.25, 9, MUTED)
add_text(s, "Win 56,09% · худшая −2,46 млн ₽ / −8,49%", 8.78, 5.63, 3.28, 0.25, 10, TEXT, True)

add_rect(s, 0.72, 6.42, 11.90, 0.45, ORANGE, True, ORANGE)
add_text(s, "ВАЖНО", 0.98, 6.54, 0.82, 0.20, 10, BG, True)
add_text(s, "High выше максимума недостаточно: ждём Close минуты > max и входим только по Open следующей минуты.", 1.82, 6.51, 10.25, 0.23, 12, BG, True)

# 13 — final risk control for the confirmed 100+100 version
s = base_slide(prs, "Финальный контроль риска", "Рекомендуемый компромисс: проверка через 30 минут + аварийный стоп 2% от текущей средней цены", 13)
cards = [
    ("+31,25 млн ₽", "net · Σ +111,00%", GREEN),
    ("2,41", "Profit Factor", TEAL),
    ("−2,79 млн ₽", "max DD · −12,67%", ORANGE),
    ("−1,15 млн ₽", "худшая · −4,06%", RED),
]
for i, (value, label, color) in enumerate(cards):
    metric_card(s, 0.72 + i * 3.02, 1.48, 2.78, value, label, color, 19)

# Rule timeline
add_rect(s, 0.72, 2.84, 7.34, 3.36, PANEL, True, GRID)
add_text(s, "КАК РАБОТАЕТ ОГРАНИЧЕНИЕ", 1.02, 3.13, 3.55, 0.25, 11, MUTED, True)
risk_steps = [
    ("1", "Первая покупка", "100 MIX на −0,4% от вершины", TEAL),
    ("2", "Через 30 минут", "Close ≤ средней — закрыть всё; Close > средней — держать", BLUE),
    ("3", "Аварийный стоп", "2% ниже текущей средней; после +100 уровень пересчитать", RED),
    ("4", "Плановый выход", "если риск-контроль не сработал — следующее торговое открытие", TEXT),
]
for i, (n, title, body, color) in enumerate(risk_steps):
    y = 3.57 + i * 0.59
    add_circle(s, 1.13, y + 0.14, 0.28, color, n, BG, 9)
    add_text(s, title, 1.40, y, 1.65, 0.24, 11, color, True)
    add_text(s, body, 3.05, y, 4.55, 0.34, 10.5, TEXT)
    if i < 3:
        add_line(s, 1.13, y + 0.29, 1.13, y + 0.58, GRID, 1.1)

# Before / after comparison
add_rect(s, 8.32, 2.84, 4.30, 3.36, PANEL_2, True, GRID)
add_text(s, "ЭФФЕКТ ПРОТИВ 100+100 БЕЗ ЗАЩИТЫ", 8.65, 3.13, 3.40, 0.38, 11, MUTED, True)
comparison = [
    ("Net", "+32,73 → +31,25 млн ₽", "−4,5%", GREEN),
    ("Profit Factor", "1,71 → 2,41", "+41%", TEAL),
    ("Max DD", "−5,89 → −2,79 млн ₽", "лучше на 53%", ORANGE),
    ("Худшая", "−2,46 → −1,15 млн ₽", "лучше на 53%", RED),
]
for i, (label, values, effect, color) in enumerate(comparison):
    y = 3.70 + i * 0.57
    add_text(s, label, 8.65, y, 1.12, 0.22, 10, MUTED, True)
    add_text(s, values, 9.75, y, 2.10, 0.22, 10.5, TEXT, True, PP_ALIGN.RIGHT)
    add_text(s, effect, 10.40, y + 0.25, 1.45, 0.18, 8.5, color, True, PP_ALIGN.RIGHT)

add_rect(s, 0.72, 6.43, 11.90, 0.43, PANEL_2, True, GRID)
add_text(s, "КАЧЕСТВО ДАННЫХ", 0.98, 6.54, 1.55, 0.20, 9, ORANGE, True)
add_text(s, "Межконтрактные гэпы скорректированы; 2 одиночных бара старого контракта 18.12.2024 исключены. Без проскальзывания.", 2.50, 6.51, 9.55, 0.23, 10.5, TEXT)

prs.save(OUT)
print(OUT)
print("slides", len(prs.slides), "equity_points", len(extract_equity()))
