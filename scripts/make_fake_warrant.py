"""Generate samples/fake_cbi_warrant.png: a deliberately FAKE 'arrest warrant' of the kind
scammers send on WhatsApp. Used only to test Kavach's document check. Watermarked as a sample."""
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "samples" / "fake_cbi_warrant.png"
FONT_DIRS = ["/usr/share/fonts/truetype/noto", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "C:/Windows/Fonts"]


def font(names, size):
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def main():
    random.seed(7)
    W, H = 1000, 1350
    img = Image.new("RGB", (W, H), (250, 247, 238))
    d = ImageDraw.Draw(img)
    hb = font(["NotoSansDevanagari-Bold.ttf", "DejaVuSans-Bold.ttf"], 34)
    hr = font(["NotoSansDevanagari-Regular.ttf", "DejaVuSans.ttf"], 24)
    eb = font(["DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf"], 30)
    er = font(["DejaVuSans.ttf", "NotoSans-Regular.ttf"], 23)
    big = font(["DejaVuSans-Bold.ttf"], 54)

    # Letterhead
    d.rectangle([0, 0, W, 150], fill=(18, 42, 99))
    d.ellipse([30, 20, 140, 130], outline=(230, 190, 60), width=6)
    d.text((62, 52), "CBI", font=eb, fill=(230, 190, 60))
    d.text((170, 22), "केंद्रीय जाँच ब्यूरो", font=hb, fill="white")
    d.text((170, 80), "CENTRAL BUREAU OF INVESTIGATON - CYBER CRIME CELL", font=er, fill="white")

    d.text((W // 2, 210), "ARREST WARRANT", font=big, fill=(170, 20, 20), anchor="mm")
    d.text((W // 2, 262), "गिरफ्तारी वारंट", font=hb, fill=(170, 20, 20), anchor="mm")

    lines = [
        (er, "Case No: CBI/MUM/DRUG/2024/00000786-XX"),
        (er, "Date: 32/13/2024        Under Section 420 IPC & NDPS Act"),
        (er, ""),
        (er, "To: Mr./Mrs. ______________________ (Aadhaar XXXX-XXXX-4521)"),
        (er, ""),
        (er, "A parcel containing 200gm MDMA drugs, 5 fake passports was"),
        (er, "seized at Mumbai customs registered on your Aadhar card."),
        (hr, "आपके आधार कार्ड पर ड्रग्स पार्सल पकड़ा गया है।"),
        (er, ""),
        (er, "You are under DIGITAL ARREST. Do not disconnect video call."),
        (hr, "यह मामला गोपनीय है, किसी को भी न बताएं - परिवार को भी नहीं।"),
        (er, ""),
        (er, "To prove innocence, transfer Rs 4,85,000 for RBI verification"),
        (er, "to Safe Custody Account below. Amount will be refunded in 24hrs."),
        (er, "  A/C: 50100XXXXXXX12   IFSC: HDFC000XXXX   UPI: cbi.verify@ybl"),
        (er, ""),
        (er, "Failure to comply within 2 hours will result in immediate arrest"),
        (er, "and your bank accounts will be freezed."),
    ]
    y = 320
    for f, t in lines:
        d.text((60, y), t, font=f, fill=(25, 25, 25))
        y += 40

    # Fake stamp + signature
    d.ellipse([640, 1030, 900, 1250], outline=(30, 60, 160), width=5)
    d.text((770, 1120), "SEAL", font=eb, fill=(30, 60, 160), anchor="mm")
    d.text((770, 1160), "Supreme Court", font=er, fill=(30, 60, 160), anchor="mm")
    d.line([(80, 1180), (360, 1150)], fill=(20, 20, 120), width=3)
    d.text((80, 1195), "Director General, CBI", font=er, fill=(30, 30, 30))

    # Watermark
    wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wm)
    wd.text((W // 2, H // 2), "SAMPLE · FOR DEMO", font=font(["DejaVuSans-Bold.ttf"], 90), fill=(200, 0, 0, 70), anchor="mm")
    wm = wm.rotate(35, resample=Image.BICUBIC)
    img = Image.alpha_composite(img.convert("RGBA"), wm).convert("RGB")

    # WhatsApp-ish: slight rotation, noise, blur, jpeg-ish softness, top bar crop
    img = img.rotate(-2.2, expand=True, fillcolor=(30, 30, 30), resample=Image.BICUBIC)
    px = img.load()
    for _ in range(25000):
        x, y = random.randrange(img.width), random.randrange(img.height)
        v = random.randint(-40, 40)
        r, g, b = px[x, y]
        px[x, y] = (max(0, min(255, r + v)), max(0, min(255, g + v)), max(0, min(255, b + v)))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    canvas = Image.new("RGB", (img.width, img.height + 70), (7, 94, 84))
    canvas.paste(img, (0, 70))
    ImageDraw.Draw(canvas).text((20, 18), "◀  +91 98XXX XXX01  ·  CBI Officer", font=er, fill="white")
    OUT.parent.mkdir(exist_ok=True)
    canvas.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
