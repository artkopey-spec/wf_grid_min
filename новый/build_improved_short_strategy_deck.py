import build_improved_strategy_deck as d
from pptx import Presentation
from pptx.util import Inches
from pptx.enum.text import PP_ALIGN
from pptx.enum.dml import MSO_LINE_DASH_STYLE


SRC = "C:/3.1_wf_grid/MIX_ST_19_4_mirror_SHORT_strategy_source.pptx"
OUT = "C:/3.1_wf_grid/MIX_ST_19_4_mirror_SHORT_strategy_improved.pptx"


def short_equity():
    src = Presentation(SRC)
    s = src.slides[3]
    segs = []
    for sh in s.shapes:
        if sh.shape_type == 9 and 1.4 <= sh.left / Inches(1) <= 8.7:
            x, y, w, h = [v / Inches(1) for v in (sh.left, sh.top, sh.width, sh.height)]
            flip_v = sh._element.spPr.xfrm.get("flipV") in ("1", "true")
            y0, y1 = (y+h, y) if flip_v else (y, y+h)
            segs.append((x, y0, x+w, y1))
    segs.sort(key=lambda z: z[0])
    pts = []
    if segs:
        pts.append((segs[0][0], segs[0][1]))
        pts.extend((z[2], z[3]) for z in segs)
    return [-5 + (5.85-y) * 25 / (5.85-3.78) for _, y in pts]


prs = Presentation()
prs.slide_width = Inches(d.W)
prs.slide_height = Inches(d.H)
prs.core_properties.title = "MIX · зеркальная SHORT-стратегия · SuperTrend 19/4"
prs.core_properties.subject = "Улучшенная визуальная версия презентации SHORT-стратегии"
prs.core_properties.author = "OpenAI Codex"
prs.core_properties.comments = "Расчётные оценки явно помечены; исходный бэктест не изменён."

# 1 — cover
s = prs.slides.add_slide(prs.slide_layouts[6])
s.background.fill.solid(); s.background.fill.fore_color.rgb = d.rgb(d.BG)
d.add_rect(s, 0, 0, 0.16, d.H, d.RED, False)
d.add_text(s, "ЗЕРКАЛЬНАЯ СТРАТЕГИЯ", 0.78, 0.73, 5.7, 0.30, 12, d.RED, True)
d.add_text(s, "MIX", 0.76, 1.15, 3.0, 0.92, 52, d.TEXT, True)
d.add_text(s, "Short · SuperTrend 19 / 4", 0.80, 2.04, 5.7, 0.48, 25, d.MUTED)
d.add_text(s, "Продажа короткого отскока\nпосле сильного падения", 0.80, 2.80, 6.4, 1.05, 27, d.TEXT, True)
d.add_rect(s, 7.45, 0.65, 5.15, 5.75, d.PANEL, True, d.GRID)
pts = [(7.90,2.18),(8.48,2.55),(9.02,2.33),(9.55,3.12),(10.05,4.02),(10.56,4.72),(11.02,4.40),(11.52,3.92),(12.08,4.24)]
for i in range(len(pts)-1):
    c = d.RED if i < 5 else (d.GREEN if i < 7 else d.ORANGE)
    d.add_line(s, *pts[i], *pts[i+1], c, 4)
d.add_line(s, 10.56, 4.72, 11.02, 4.40, d.GREEN, 1.2, MSO_LINE_DASH_STYLE.DASH)
d.add_circle(s, 11.02, 4.40, 0.20, d.RED)
d.add_text(s, "+0,1% · SHORT", 10.25, 3.86, 1.55, 0.25, 11, d.RED, True, PP_ALIGN.CENTER)
d.add_text(s, ">0,5%", 9.43, 4.76, 1.35, 0.36, 20, d.RED, True, PP_ALIGN.CENTER)
d.add_text(s, "импульс вниз", 9.34, 5.10, 1.55, 0.22, 9, d.MUTED, False, PP_ALIGN.CENTER)
d.add_text(s, "19:00", 11.48, 3.52, 0.76, 0.24, 10, d.TEXT, True, PP_ALIGN.CENTER)
d.metric_card(s, 0.80, 5.25, 2.18, "+15,47", "млн ₽ net", d.RED, 23)
d.metric_card(s, 3.15, 5.25, 1.85, "1,33", "Profit Factor", d.ORANGE, 23)
d.metric_card(s, 5.17, 5.25, 1.85, "56,1%", "win rate", d.BLUE, 23)
d.add_text(s, "Бэктест · 100 MIX · все сигналы сохранены", 0.82, 6.66, 6.6, 0.24, 10, d.MUTED)
d.add_text(s, "01", 12.10, 7.10, 0.45, 0.22, 9, d.MUTED, True, PP_ALIGN.RIGHT)

# 2 — overview
s = d.base_slide(prs, "SHORT-стратегия на одном слайде", "Первый импульс вниз → зелёное подтверждение → короткий отскок → фиксированное 19:00", 2)
stages = [
    ("1", "ИМПУЛЬС ВНИЗ", "Первый красный ST дня\nстрого > 0,5%", d.RED),
    ("2", "СМЕНА ST", "Красный → зелёный;\nфиксируем минимум", d.GREEN),
    ("3", "ВХОД SHORT", "Продать 100 MIX\nна +0,1% от минимума", d.ORANGE),
    ("4", "ВЫХОД", "Ближайшие торговые\n19:00 после сигнала", d.TEXT),
]
for i,(n,t,b,c) in enumerate(stages):
    x=0.72+i*3.02
    d.add_rect(s,x,1.60,2.72,2.10,d.PANEL,True,d.GRID)
    d.add_circle(s,x+0.34,1.94,0.38,c,n,d.BG,12)
    d.add_text(s,t,x+0.64,1.79,1.77,0.35,11,c,True)
    d.add_text(s,b,x+0.25,2.43,2.20,0.78,13,d.TEXT,True)
    if i<3: d.add_text(s,"→",x+2.73,2.38,0.28,0.35,18,d.MUTED,True,PP_ALIGN.CENTER)
d.add_rect(s,0.72,4.08,7.48,1.76,d.PANEL_2,True,d.GRID)
d.add_text(s,"КЛЮЧЕВАЯ ИДЕЯ",1.02,4.35,2.4,0.25,11,d.RED,True)
d.add_text(s,"После сильного падения продавать\nне минимум, а короткий подтверждённый отскок.",1.02,4.78,6.75,0.68,20,d.TEXT,True)
d.add_rect(s,8.46,4.08,4.16,1.76,d.PANEL,True,d.GRID)
d.add_text(s,"ЕСЛИ СИГНАЛ ПОСЛЕ 19:00",8.77,4.35,3.20,0.25,11,d.ORANGE,True)
d.add_text(s,"позиция переносится до следующих\nторговых 19:00",8.77,4.79,3.15,0.60,15,d.TEXT,True)
d.add_text(s,"Это сохраняет edge, но создаёт отдельный overnight-риск.",0.78,6.25,9.0,0.28,11,d.MUTED,False,italic=True)

# 3 — anatomy
s = d.base_slide(prs, "Анатомия зеркальной сделки", "Подтверждение тренда задаёт ST, а +0,1% лишь улучшает цену входа", 3)
d.add_rect(s,0.72,1.55,11.90,3.72,d.PANEL,True,d.GRID)
for y in [2.25,3.05,3.85,4.65]: d.add_line(s,1.08,y,12.25,y,d.GRID,0.7,MSO_LINE_DASH_STYLE.DASH)
path=[(1.05,2.20),(1.68,2.53),(2.28,2.38),(2.92,3.08),(3.56,3.84),(4.20,4.62),(4.74,4.37),(5.32,4.02),(5.95,4.22),(6.60,4.48),(7.28,4.13),(8.00,4.33),(8.76,4.06),(9.65,4.02),(10.65,4.00),(11.82,3.98)]
for i in range(len(path)-1):
    c = d.RED if i<5 else (d.GREEN if i<7 else d.ORANGE)
    d.add_line(s,*path[i],*path[i+1],c,3.6)
d.add_circle(s,4.20,4.62,0.17,d.RED)
d.add_circle(s,5.32,4.02,0.20,d.RED)
d.add_circle(s,11.82,3.98,0.18,d.TEXT)
d.add_text(s,"МИНИМУМ",3.72,4.78,1.00,0.22,9,d.RED,True,PP_ALIGN.CENTER)
d.add_text(s,"+0,1% · SHORT",4.55,3.56,1.58,0.24,10,d.RED,True,PP_ALIGN.CENTER)
d.add_text(s,"19:00",11.42,3.50,0.80,0.22,9,d.TEXT,True,PP_ALIGN.CENTER)
d.add_line(s,4.65,3.32,6.25,3.32,d.GREEN,2.2)
d.add_text(s,"зелёный ST",4.88,3.02,1.20,0.22,9,d.GREEN,True,PP_ALIGN.CENTER)
labels=[("Импульс >0,5%",d.RED),("Смена ST",d.GREEN),("Short +0,1%",d.ORANGE),("Выход 19:00",d.TEXT)]
for i,(t,c) in enumerate(labels):
    x=0.86+i*3.02
    d.add_circle(s,x+0.08,5.74,0.11,c)
    d.add_text(s,t,x+0.22,5.62,2.40,0.25,11,d.TEXT,True)
d.add_rect(s,0.72,6.20,11.90,0.60,d.PANEL_2,True,d.GRID)
d.add_text(s,"Важное отличие от LONG:",1.00,6.36,2.55,0.24,11,d.RED,True)
d.add_text(s,"глубокого отката ждать не нужно — зелёное переключение ST уже подтверждает завершение падения",3.40,6.36,8.68,0.24,11,d.TEXT)

# 4 — edge and funnel
s = d.base_slide(prs, "Откуда может браться преимущество", "Объясняющая гипотеза: импульс отделяет режим, ST подтверждает разворот, 19:00 ограничивает горизонт", 4)
ideas=[
    ("КАПИТУЛЯЦИЯ",">0,5% вниз выделяет сильное внутридневное движение, способное продолжиться после паузы.",d.RED,"01"),
    ("НЕ ПРОДАВАТЬ МИНИМУМ","Зелёный ST и +0,1% дают вход после локальной стабилизации цены.",d.GREEN,"02"),
    ("ФИКСИРОВАННЫЙ ЧАС","19:00 стандартизирует удержание, но вечерние сигналы переносят риск overnight.",d.ORANGE,"03"),
]
for i,(t,b,c,n) in enumerate(ideas):
    y=1.60+i*1.42
    d.add_rect(s,0.72,y,7.15,1.15,d.PANEL,True,d.GRID)
    d.add_text(s,n,1.00,y+0.18,0.62,0.35,18,c,True)
    d.add_text(s,t,1.78,y+0.16,2.92,0.28,11,c,True)
    d.add_text(s,b,4.60,y+0.13,2.88,0.68,10.5,d.TEXT)
d.add_rect(s,8.15,1.60,4.47,4.00,d.PANEL_2,True,d.GRID)
d.add_text(s,"ВОРОНКА БЭКТЕСТА",8.52,1.92,3.55,0.28,11,d.MUTED,True)
funnel=[("492","сигнала",4.00,d.ORANGE),("492","исполнено",3.25,d.RED),("≈276","прибыльных",2.50,d.GREEN)]
for i,(v,l,w,c) in enumerate(funnel):
    x=10.385-w/2; y=2.50+i*0.88
    d.add_rect(s,x,y,w,0.67,c,True,c)
    d.add_text(s,v,x+0.15,y+0.10,0.92,0.30,18,d.BG,True)
    d.add_text(s,l,x+1.05,y+0.15,w-1.20,0.24,10,d.BG,True,PP_ALIGN.RIGHT)
d.add_text(s,"100% fill rate",8.60,5.04,3.55,0.27,12,d.RED,True,PP_ALIGN.CENTER)
d.add_rect(s,0.72,6.10,11.90,0.66,d.PANEL,True,d.GRID)
d.add_text(s,"Проверить отдельно:",1.00,6.29,2.10,0.24,11,d.ORANGE,True)
d.add_text(s,"стабильность по годам, overnight-gap, доступность short и проскальзывание на входе/19:00",3.00,6.29,9.10,0.24,11,d.TEXT)

# 5 — parameters
s = d.base_slide(prs, "Параметры образуют устойчивую область", "110 комбинаций: импульс 0,3–1,2% и отскок 0–1,0%, шаг 0,1 п.п.", 5)
d.add_rect(s,0.72,1.55,7.40,4.92,d.PANEL,True,d.GRID)
d.add_text(s,"ЛУЧШИЕ КОМБИНАЦИИ · NET МЛН ₽",1.03,1.85,4.40,0.28,12,d.MUTED,True)
params=[(">0,5% / +0,1%",15.47,d.RED),(">0,5% / сразу",15.43,d.ORANGE),(">0,5% / +0,2%",14.75,d.GREEN),(">0,4% / +0,2%",13.72,d.TEAL),(">0,6% / +0,1%",13.10,d.BLUE)]
for i,(lab,val,c) in enumerate(params):
    y=2.38+i*0.66
    d.add_text(s,lab,1.03,y,1.72,0.27,11,d.TEXT,True)
    d.add_rect(s,2.82,y+0.04,3.30,0.22,d.GRID,True,d.GRID)
    d.add_rect(s,2.82,y+0.04,3.30*val/15.47,0.22,c,True,c)
    d.add_text(s,f"{val:.2f}",6.35,y-0.01,0.82,0.27,11,c,True,PP_ALIGN.RIGHT)
d.add_text(s,"Разница №1 и №2 — всего 0,04 млн ₽",1.03,5.91,5.80,0.26,11,d.ORANGE,True)
d.add_rect(s,8.39,1.55,4.23,4.92,d.PANEL_2,True,d.GRID)
d.add_text(s,"УСТОЙЧИВАЯ ОБЛАСТЬ",8.74,1.89,3.30,0.28,11,d.MUTED,True)
d.add_text(s,"Импульс",8.74,2.52,1.30,0.25,11,d.MUTED)
d.add_text(s,"> 0,5%",10.14,2.43,1.62,0.42,23,d.RED,True)
d.add_text(s,"Отскок",8.74,3.22,1.30,0.25,11,d.MUTED)
d.add_text(s,"0–0,2%",10.14,3.13,1.62,0.42,23,d.GREEN,True)
d.add_line(s,8.74,3.85,12.18,3.85,d.GRID,1)
d.add_text(s,"Более осторожный вариант",8.74,4.18,3.12,0.25,11,d.ORANGE,True)
d.add_text(s,">0,7% / +0,1%",8.74,4.60,3.15,0.32,17,d.TEXT,True)
d.add_text(s,"DD −2,41 млн ₽ · Σ −11,56%",8.74,5.05,3.20,0.25,10,d.MUTED)
d.add_text(s,"Вывод: выбор +0,1% не выглядит одиночным пиком — рядом находятся почти равные решения.",0.78,6.68,11.30,0.24,11,d.GREEN,False,italic=True)

# 6 — results
s = d.base_slide(prs, "Результат стратегии", "100 MIX · комиссии включены · межконтрактные переходы скорректированы", 6)
cards=[("+15,47 млн ₽","net · Σ +53,08%",d.RED),("56,1%","прибыльных",d.GREEN),("1,33","Profit Factor",d.ORANGE),("−3,66 млн ₽","max DD · Σ −13,15%",d.BLUE)]
for i,(v,l,c) in enumerate(cards): d.metric_card(s,0.72+i*3.02,1.52,2.78,v,l,c,20)
d.add_rect(s,0.72,2.86,8.55,3.72,d.PANEL,True,d.GRID)
d.add_text(s,"НАКОПЛЕННЫЙ P&L · МЛН ₽",1.02,3.13,4.0,0.25,11,d.MUTED,True)
vals=short_equity(); x0,y0,pw,ph=1.22,3.68,7.55,2.35
for val in [-5,0,5,10,15,20]:
    yy=y0+ph-((val+5)/25)*ph
    d.add_line(s,x0,yy,x0+pw,yy,d.GRID,0.6)
    d.add_text(s,str(val),0.87,yy-0.10,0.30,0.18,8,d.MUTED,False,PP_ALIGN.RIGHT)
if vals:
    for i in range(len(vals)-1):
        xa=x0+pw*i/(len(vals)-1); xb=x0+pw*(i+1)/(len(vals)-1)
        ya=y0+ph-((vals[i]+5)/25)*ph; yb=y0+ph-((vals[i+1]+5)/25)*ph
        d.add_line(s,xa,ya,xb,yb,d.RED,1.7)
    d.add_circle(s,x0+pw,y0+ph-((vals[-1]+5)/25)*ph,0.16,d.GREEN)
d.add_text(s,"1",1.14,6.10,0.32,0.20,8,d.MUTED)
d.add_text(s,"492",8.38,6.10,0.40,0.20,8,d.MUTED,False,PP_ALIGN.RIGHT)
d.add_rect(s,9.52,2.86,3.10,3.72,d.PANEL_2,True,d.GRID)
d.add_text(s,"ВЫБОРКА",9.84,3.14,1.50,0.24,11,d.MUTED,True)
d.add_text(s,"630 299",9.84,3.64,2.28,0.40,24,d.TEXT,True)
d.add_text(s,"минутных баров",9.84,4.07,2.28,0.24,10,d.MUTED)
d.add_line(s,9.84,4.55,12.26,4.55,d.GRID,1)
d.add_text(s,"492",9.84,4.86,0.82,0.35,19,d.ORANGE,True)
d.add_text(s,"сигнала",10.74,4.91,1.20,0.24,10,d.MUTED)
d.add_text(s,"492",9.84,5.40,0.82,0.35,19,d.RED,True)
d.add_text(s,"сделки",10.74,5.45,1.20,0.24,10,d.MUTED)
d.add_text(s,"100% исполнено",9.84,6.08,2.24,0.25,11,d.TEXT,True)

# 7 — evening signals
s = d.base_slide(prs, "Вечерние сигналы усиливают результат", "30 сделок после 19:00 дали значимый вклад, но требуют отдельного overnight-лимита", 7)
d.add_rect(s,0.72,1.55,5.70,3.10,d.PANEL,True,d.GRID)
d.add_text(s,"ВСЕ СИГНАЛЫ",1.03,1.87,2.40,0.25,11,d.RED,True)
d.add_text(s,"+15,47 млн ₽",1.03,2.36,3.70,0.52,30,d.TEXT,True)
d.add_text(s,"492 сделки · PF 1,33",1.03,3.00,3.65,0.27,12,d.MUTED)
d.add_text(s,"DD −3,66 млн ₽ · Σ −13,15%",1.03,3.49,3.85,0.27,11,d.MUTED)
d.add_rect(s,6.67,1.55,5.95,3.10,d.PANEL,True,d.GRID)
d.add_text(s,"ТОЛЬКО ДО 19:00",6.98,1.87,2.80,0.25,11,d.ORANGE,True)
d.add_text(s,"+12,02 млн ₽",6.98,2.36,3.70,0.52,30,d.TEXT,True)
d.add_text(s,"462 сделки · PF 1,26",6.98,3.00,3.65,0.27,12,d.MUTED)
d.add_text(s,"DD −3,25 млн ₽ · Σ −13,15%",6.98,3.49,3.85,0.27,11,d.MUTED)
d.add_rect(s,0.72,4.94,11.90,1.42,d.PANEL_2,True,d.RED)
d.add_text(s,"+3,44 млн ₽",1.03,5.22,2.52,0.45,25,d.RED,True)
d.add_text(s,"вклад 30 вечерних сигналов",3.40,5.28,2.78,0.28,12,d.TEXT,True)
d.add_line(s,6.40,5.20,6.40,6.05,d.GRID,1)
d.add_text(s,"≈ +115 тыс. ₽",6.73,5.22,2.28,0.38,21,d.GREEN,True)
d.add_text(s,"на вечернюю сделку",8.95,5.29,2.23,0.26,11,d.TEXT)
d.add_text(s,"Цена преимущества — перенос позиции до следующих торговых 19:00.",0.78,6.68,10.0,0.24,11,d.ORANGE,False,italic=True)

# 8 — economics
s = d.base_slide(prs, "Экономика одной сделки", "Число исходов и средняя прибыль — расчётная оценка из win rate, PF и среднего убытка", 8)
d.add_rect(s,0.72,1.55,7.00,4.85,d.PANEL,True,d.GRID)
d.add_text(s,"≈276",1.05,1.98,1.52,0.54,30,d.GREEN,True)
d.add_text(s,"прибыльных",1.05,2.48,1.52,0.24,10,d.MUTED)
d.add_text(s,"≈216",5.89,1.98,1.40,0.54,30,d.RED,True,PP_ALIGN.RIGHT)
d.add_text(s,"убыточных",5.62,2.48,1.67,0.24,10,d.MUTED,False,PP_ALIGN.RIGHT)
d.add_line(s,1.20,3.30,7.20,3.30,d.GRID,8)
d.add_line(s,1.20,3.30,1.20+6*0.561,3.30,d.GREEN,8)
d.add_circle(s,1.20+6*0.561,3.30,0.20,d.TEXT)
d.add_text(s,"56,1%",3.14,2.86,1.45,0.30,15,d.TEXT,True,PP_ALIGN.CENTER)
d.add_rect(s,1.02,3.90,2.72,1.56,d.PANEL_2,True,d.GRID)
d.add_text(s,"≈ +229 тыс. ₽",1.28,4.18,2.20,0.36,20,d.GREEN,True)
d.add_text(s,"средняя прибыль · оценка",1.28,4.73,2.15,0.24,10,d.MUTED)
d.add_rect(s,4.02,3.90,2.72,1.56,d.PANEL_2,True,d.GRID)
d.add_text(s,"−220 тыс. ₽",4.28,4.18,2.20,0.36,20,d.RED,True)
d.add_text(s,"средний убыток · факт",4.28,4.73,2.15,0.24,10,d.MUTED)
d.add_text(s,"Соотношение ≈ 1,04 : 1",2.24,5.83,3.90,0.28,12,d.ORANGE,True,PP_ALIGN.CENTER)
d.add_rect(s,7.98,1.55,4.64,4.85,d.PANEL_2,True,d.GRID)
d.add_text(s,"ОЖИДАЕМАЯ СДЕЛКА",8.35,1.92,3.50,0.25,11,d.MUTED,True)
d.add_text(s,"+31 434 ₽",8.35,2.50,3.50,0.62,34,d.GREEN,True)
d.add_text(s,"+0,108% к номиналу входа",8.35,3.16,3.50,0.28,12,d.TEXT)
d.add_line(s,8.35,3.76,12.20,3.76,d.GRID,1)
d.add_text(s,"Профиль тоньше, чем у LONG",8.35,4.10,3.50,0.25,12,d.ORANGE,True)
d.add_text(s,"Средний выигрыш почти равен среднему убытку. Положительный результат сильнее зависит от стабильности win rate и качества исполнения.",8.35,4.54,3.48,1.18,12,d.TEXT)
d.add_text(s,"≈ — производная оценка, возможны округления.",8.35,5.93,3.50,0.20,8,d.MUTED,False,italic=True)

# 9 — loss profile
s = d.base_slide(prs, "Риск SHORT — новый рост после отскока", "После входа падение может не продолжиться; вечерние сделки добавляют overnight-gap", 9)
d.metric_card(s,0.72,1.52,2.80,"−159 тыс. ₽","медианный · −0,570%",d.RED,20)
d.metric_card(s,3.69,1.52,2.80,"−220 тыс. ₽","средний · −0,798%",d.RED,20)
d.metric_card(s,6.66,1.52,2.80,"−1,10 млн ₽","худшая · −4,417%",d.ORANGE,20)
d.metric_card(s,9.63,1.52,2.99,"−3,66 млн ₽","max DD · Σ −13,15%",d.BLUE,20)
d.add_rect(s,0.72,2.92,7.38,3.48,d.PANEL,True,d.GRID)
d.add_text(s,"ЦЕПОЧКА РИСКА",1.02,3.20,3.2,0.26,11,d.MUTED,True)
risks=[("Short открыт","+0,1% от минимума",d.RED),("Падение не вернулось","ST начинает новый рост",d.GREEN),("Убыток расширяется","Short против импульса вверх",d.ORANGE),("Вечерний сигнал","добавляется overnight-gap",d.BLUE)]
for i,(t,b,c) in enumerate(risks):
    y=3.70+i*0.60
    d.add_circle(s,1.17,y+0.16,0.28,c,i+1,d.BG,10)
    d.add_text(s,t,1.46,y,2.28,0.25,12,d.TEXT,True)
    d.add_text(s,b,3.95,y,3.55,0.25,11,c,True,PP_ALIGN.RIGHT)
    if i<3: d.add_line(s,1.17,y+0.31,1.17,y+0.62,d.GRID,1.2)
d.add_rect(s,8.35,2.92,4.27,3.48,d.PANEL_2,True,d.GRID)
d.add_text(s,"КОНТРОЛЬ",8.72,3.28,2.00,0.25,11,d.RED,True)
controls=["фиксированный размер 100 MIX","отдельный overnight-лимит","никогда не усреднять Short","выход 19:00 без переноса решения"]
for i,t in enumerate(controls):
    d.add_circle(s,8.82,3.85+i*0.52,0.10,d.RED)
    d.add_text(s,t,9.04,3.72+i*0.52,2.82,0.28,11,d.TEXT)
d.add_text(s,"Стоп-параметры для этой версии отдельно не оптимизировались.",0.78,6.68,11.30,0.24,11,d.ORANGE,False,italic=True)

# 10 — Long vs Short
s = d.base_slide(prs, "LONG и SHORT — не зеркальные копии", "Идея симметрична, но пороги, горизонт и профиль преимущества различаются", 10)
d.add_rect(s,0.72,1.55,5.82,4.85,d.PANEL,True,d.GRID)
d.add_text(s,"LONG",1.04,1.88,1.30,0.36,20,d.GREEN,True)
d.add_text(s,">0,7% → −0,4%",1.04,2.43,3.30,0.38,19,d.TEXT,True)
d.add_text(s,"выход: следующее открытие",1.04,2.90,3.80,0.25,11,d.MUTED)
d.add_text(s,"+20,40 млн ₽",1.04,3.52,3.20,0.44,25,d.GREEN,True)
d.add_text(s,"59,8% win · PF 1,64",1.04,4.14,3.65,0.27,12,d.TEXT)
d.add_text(s,"271 сделка",1.04,4.64,1.55,0.28,12,d.MUTED,True)
d.add_text(s,"DD −3,97 млн ₽ · Σ −12,83%",1.04,5.16,3.85,0.27,11,d.MUTED)
d.add_rect(s,6.80,1.55,5.82,4.85,d.PANEL_2,True,d.GRID)
d.add_text(s,"SHORT",7.12,1.88,1.70,0.36,20,d.RED,True)
d.add_text(s,">0,5% → +0,1%",7.12,2.43,3.30,0.38,19,d.TEXT,True)
d.add_text(s,"выход: ближайшие 19:00",7.12,2.90,3.80,0.25,11,d.MUTED)
d.add_text(s,"+15,47 млн ₽",7.12,3.52,3.20,0.44,25,d.RED,True)
d.add_text(s,"56,1% win · PF 1,33",7.12,4.14,3.65,0.27,12,d.TEXT)
d.add_text(s,"492 сделки",7.12,4.64,1.55,0.28,12,d.MUTED,True)
d.add_text(s,"DD −3,66 млн ₽ · Σ −13,15%",7.12,5.16,3.85,0.27,11,d.MUTED)
d.add_rect(s,0.72,6.58,11.90,0.25,d.RED,True,d.RED)
d.add_text(s,"SHORT торгует чаще, но имеет меньший запас по PF и ожидаемой сделке.",1.30,6.52,10.75,0.35,12,d.BG,True,PP_ALIGN.CENTER)

# 11 — launch
s = d.base_slide(prs, "Итоговый алгоритм и запуск", "Все правила формализованы; перед капиталом нужны OOS и проверка исполнения", 11)
steps=[
    ("01","НАЙТИ ИМПУЛЬС","Первый красный ST дня строго >0,5%",d.RED),
    ("02","ЗАФИКСИРОВАТЬ MIN","Дождаться переключения ST красный → зелёный",d.GREEN),
    ("03","ОТКРЫТЬ SHORT","100 MIX на +0,1% от минимума",d.ORANGE),
    ("04","ВЫБРАТЬ 19:00","До 19:00 — сегодня; после — следующий торговый день",d.BLUE),
    ("05","ЗАКРЫТЬ ВСЁ","В 19:00 · без усреднения и ручного переноса",d.TEXT),
]
for i,(n,t,b,c) in enumerate(steps):
    y=1.49+i*0.88
    d.add_text(s,n,0.80,y+0.11,0.52,0.30,14,c,True)
    d.add_rect(s,1.48,y,6.62,0.70,d.PANEL,True,d.GRID)
    d.add_text(s,t,1.74,y+0.10,2.35,0.25,11,c,True)
    d.add_text(s,b,4.05,y+0.08,3.68,0.42,10.5,d.TEXT)
d.add_rect(s,8.38,1.49,4.24,4.22,d.PANEL_2,True,d.GRID)
d.add_text(s,"ПЕРЕД РЕАЛЬНЫМ ЗАПУСКОМ",8.74,1.83,3.40,0.52,13,d.ORANGE,True)
checks=["walk-forward / OOS по годам","проскальзывание на 19:00","short-доступность и ликвидность","overnight и дневной лимит риска","пилот на малом размере"]
for i,t in enumerate(checks):
    d.add_circle(s,8.84,2.72+i*0.53,0.11,d.ORANGE)
    d.add_text(s,t,9.05,2.59+i*0.53,2.95,0.30,11,d.TEXT)
d.add_rect(s,0.72,6.08,11.90,0.66,d.RED,True,d.RED)
d.add_text(s,"GO / NO-GO",1.02,6.29,1.38,0.23,11,d.BG,True)
d.add_text(s,"Запускать только после положительного OOS и подтверждения overnight-исполнения.",2.42,6.24,9.30,0.31,14,d.BG,True)

prs.save(OUT)
print(OUT)
print("slides", len(prs.slides), "equity_points", len(short_equity()))
