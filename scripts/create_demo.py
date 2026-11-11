"""Small synthetic deck with speaker notes and native diagrams for pipeline checks."""
from pathlib import Path
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt


def create_demo(destination: Path):
    p = Presentation()
    p.slide_width, p.slide_height = Inches(13.333), Inches(7.5)
    specifications = [
        ("Solar output varies", "Clouds and daylight change how much power a solar panel produces.",
         [("SUN / CLOUD", "F2C95D"), ("SOLAR PANEL", "4C99CA"), ("VARIABLE POWER", "E5A084")],
         "Explain why clouds reduce solar generation. Finish this explanation before transitioning to the next diagram about storing surplus power."),
        ("Store surplus power", "A battery stores daytime surplus and supplies the load after sunset.",
         [("SOLAR SURPLUS", "4C99CA"), ("BATTERY", "66B6A3"), ("EVENING LOAD", "8D9FCE")],
         "Use the arrows to explain charging and discharging. Only move on when this mechanism is clear, then introduce the capacity versus cost decision."),
        ("Choose capacity against cost", "More storage covers longer gaps, but raises the upfront investment.",
         [("SMALLER BATTERY\nLower cost", "66B6A3"), ("TRADEOFF", "F2C95D"), ("LARGER BATTERY\nMore coverage", "8D9FCE")],
         "Compare usable capacity and investment. This is the final slide; do not advance automatically."),
    ]
    for number, (title, body, boxes, notes) in enumerate(specifications, 1):
        slide = p.slides.add_slide(p.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string("F5F7FA")
        for text, top, size, color in [(title, 1.0, 38, "152F49"), (body, 2.0, 22, "40566B")]:
            box = slide.shapes.add_textbox(Inches(.8), Inches(top), Inches(11.8), Inches(.9))
            run = box.text_frame.paragraphs[0].add_run()
            run.text, run.font.size, run.font.color.rgb = text, Pt(size), RGBColor.from_string(color)
        for i, (label, color) in enumerate(boxes):
            box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(.8 + i * 4.05), Inches(3.6), Inches(3.3), Inches(1.5))
            box.fill.solid()
            box.fill.fore_color.rgb = RGBColor.from_string(color)
            box.line.fill.background()
            box.text = label
            for para in box.text_frame.paragraphs:
                para.font.size = Pt(22)
                para.font.bold = True
                para.font.color.rgb = RGBColor.from_string("152F49")
            if i < 2:
                arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(4.2 + i * 4.05), Inches(4.05), Inches(.55), Inches(.4))
                arrow.fill.solid()
                arrow.fill.fore_color.rgb = RGBColor.from_string("40566B")
        footer = slide.shapes.add_textbox(Inches(.8), Inches(6.6), Inches(11), Inches(.3))
        footer.text = f"Jev slide-switcher · synthetic demonstration · {number}/3"
        slide.notes_slide.notes_text_frame.text = notes
    destination.parent.mkdir(parents=True, exist_ok=True)
    p.save(destination)
    return destination


if __name__ == "__main__":
    print(create_demo(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "examples/solar_storage.pptx"))
