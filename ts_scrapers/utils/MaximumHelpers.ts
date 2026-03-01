
import { normalizeBankName, normalizeCardName } from './bankMapper';

// --- HELPER FUNCTIONS PORTED FROM PYTHON ---

export const trLower = (text: string): string => {
    return text ? text.replace(/I/g, 'ı').replace(/İ/g, 'i').toLowerCase() : "";
};

export const cleanText = (text: string): string => {
    if (!text) return "";
    return text.replace(/\n/g, ' ').replace(/\r/g, '').replace(/\s+/g, ' ').trim();
};

export const formatNumber = (num: number): string | null => {
    try {
        return Math.floor(num).toLocaleString('tr-TR').replace(/,/g, '.'); // Python format logic mimic
    } catch {
        return null;
    }
};

export const formatDateIso = (dateStr: string, isEnd: boolean = false): string | null => {
    if (!dateStr) return null;
    const ts = trLower(dateStr);
    const months: { [key: string]: string } = {
        'ocak': '01', 'şubat': '02', 'mart': '03', 'nisan': '04', 'mayıs': '05', 'haziran': '06',
        'temmuz': '07', 'ağustos': '08', 'eylül': '09', 'ekim': '10', 'kasım': '11', 'aralık': '12'
    };

    try {
        // dd.mm.yyyy - dd.mm.yyyy
        const mDot = ts.match(/(\d{1,2})\.(\d{1,2})\.(\d{4})\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})/);
        if (mDot) {
            const [_, g1, a1, y1, g2, a2, y2] = mDot;
            if (isEnd) return `${y2}-${a2.padStart(2, '0')}-${g2.padStart(2, '0')}T23:59:59Z`;
            else return `${y1}-${a1.padStart(2, '0')}-${g1.padStart(2, '0')}T00:00:00Z`;
        }

        // dd month - dd month yyyy
        const m = ts.match(/(\d{1,2})\s*([a-zğüşıöç]+)?\s*-\s*(\d{1,2})\s*([a-zğüşıöç]+)\s*(\d{4})/);
        if (m) {
            let [_, g1, a1, g2, a2, yil] = m;
            if (!a1) a1 = a2;
            const monthCode1 = months[a1] || '01';
            const monthCode2 = months[a2] || '12';

            if (isEnd) return `${yil}-${monthCode2}-${g2.padStart(2, '0')}T23:59:59Z`;
            else return `${yil}-${monthCode1}-${g1.padStart(2, '0')}T00:00:00Z`;
        }
    } catch (e) { return null; }
    return null;
};

export const getCategory = (title: string, text: string): string => {
    const t = trLower(title + " " + text);
    if (["market", "bakkal", "süpermarket", "migros"].some(x => t.includes(x))) return "Market";
    if (["restoran", "kafe", "yemek", "burger"].some(x => t.includes(x))) return "Restoran & Kafe";
    if (["akaryakıt", "benzin", "otogaz", "opet", "shell"].some(x => t.includes(x))) return "Yakıt";
    if (["giyim", "moda", "ayakkabı"].some(x => t.includes(x))) return "Giyim & Moda";
    if (["elektronik", "teknoloji", "telefon"].some(x => t.includes(x))) return "Elektronik";
    if (["seyahat", "otel", "uçak", "tatil"].some(x => t.includes(x))) return "Seyahat";
    if (["e-ticaret", "online", "internet", "trendyol"].some(x => t.includes(x))) return "Online Alışveriş";
    return "Diğer";
};

export const extractMerchant = (title: string): string | null => {
    try {
        const match = title.match(/(.+?)['’](?:ta|te|tan|ten|da|de|dan|den)\s/i);
        if (match) {
            const merchant = match[1].trim();
            if (merchant.split(/\s+/).length < 5) return merchant;
        }
    } catch { }
    return null;
};

// --- CARD FILTER ---
export const extractCardsPrecise = (text: string): string[] => {
    const includeSection = text.match(/(?:Kampanyaya|Kampanya)\s+(?:dâhil|dahil)\s+(?:olan|edilen)\s+(?:kartlar|işlemler|kartlar ve işlemler)\s*:?\s*(.*?)(?:Kampanyaya\s+(?:dâhil|dahil)\s+(?:olmayan)|$)/is);

    const targetText = includeSection ? includeSection[1] : text;
    const tLow = targetText.replace(/İ/g, 'i').toLowerCase();

    const cardPatterns: [string, RegExp][] = [
        ["Maximiles Black", /maximiles\s+black/], ["Maximiles", /maximiles(?!.*\sblack)/],
        ["Privia Black", /privia\s+black/], ["Privia", /privia(?!.*\sblack)/],
        ["MercedesCard", /mercedes\s*card|mercedes/],
        ["İş'te Üniversiteli", /iş['’\s]?te\s+üniversiteli/],
        ["Maximum Genç", /maximum\s+genç|genç\s+kart/],
        ["Maximum Pati Kart", /pati\s+kart/], ["Maximum TEMA Kart", /tema\s+kart/],
        ["Maximum Gold", /maximum\s+gold/], ["Maximum Platinum", /maximum\s+platinum/],
        ["Maximum Premier", /maximum\s+premier/], ["Bankamatik Kartı", /bankamatik/],
        ["MaxiPara", /maxipara/], ["Ticari Kart", /ticari|vadematik|şirket\s+kredi/],
        ["Sanal Kart", /sanal\s+kart/], ["TROY Logolu Kart", /troy/],
        ["Maximum Kart", /maximum\s+kart|maximum\s+özellikli/]
    ];

    const foundCards: string[] = [];
    for (const [name, pattern] of cardPatterns) {
        if (tLow.match(pattern)) {
            if (name === "Maximiles" && foundCards.includes("Maximiles Black")) continue;
            if (name === "Privia" && foundCards.includes("Privia Black")) continue;
            if (!foundCards.includes(name)) foundCards.push(name);
        }
    }
    if (foundCards.length === 0) {
        if (tLow.includes("bireysel") && tLow.includes("kredi kartı")) foundCards.push("Maximum Kart");
    }
    // Sort and return
    return Array.from(new Set(foundCards)).sort();
};

// --- FINANCIAL ENGINE V8 ---
export const extractFinancialsV8 = (text: string, title: string) => {
    const textClean = text.replace(/(?<=\d)\.(?=\d)/g, ''); // 1.000 -> 1000 logic needs careful regex in JS
    // JS doesn't support lookbehind in all envs, but basic replacement is fine for now
    // Simpler: remove dots between digits
    const textNoDots = text.replace(/(\d)\.(\d)/g, '$1$2');

    const tLow = textNoDots.replace(/İ/g, 'i').toLowerCase();
    const titleLow = title.replace(/İ/g, 'i').toLowerCase();

    let minS = 0;
    let maxD = 0;
    let earn: string | null = null;
    let disc: string | null = null;

    // 1. Installments
    const titleTaksit = titleLow.match(/(\d+)\s*(?:aya varan)?\s*taksit/);
    if (titleTaksit && parseInt(titleTaksit[1]) < 24) {
        disc = `${titleTaksit[1]} Taksit`;
    } else if (tLow.includes("taksit")) {
        const pesinM = [...tLow.matchAll(/peşin fiyatına\s*(\d+)\s*taksit/g)];
        if (pesinM.length > 0) {
            const vals = pesinM.map(m => parseInt(m[1]));
            disc = `${Math.max(...vals)} Taksit`;
        } else {
            const taksitM = [...tLow.matchAll(/(\d+)\s*(?:aya varan|ay)?\s*taksit/g)];
            const validT = taksitM.map(m => parseInt(m[1])).filter(t => t >= 2 && t <= 18);
            if (validT.length > 0) disc = `${Math.max(...validT)} Taksit`;
        }
    }

    if (disc) {
        const rangeMatch = tLow.match(/(\d+)\s*(?:-|ile)\s*(\d+)\s*tl.*?taksit/);
        if (rangeMatch) minS = parseInt(rangeMatch[1]);
        else {
            const sMatch = tLow.match(/(\d+)\s*tl.*?taksit/);
            if (sMatch) minS = parseInt(sMatch[1]);
        }
    }

    // 2. Price Advantage
    const priceMatch = tLow.match(/(\d+)\s*tl\s*yerine\s*(\d+)\s*tl/);
    if (priceMatch) {
        const oldP = parseInt(priceMatch[1]);
        const newP = parseInt(priceMatch[2]);
        if (oldP - newP > 0) {
            maxD = oldP - newP;
            earn = `${maxD.toLocaleString('tr-TR')} TL İndirim (Fiyat Avantajı)`;
            minS = newP;
            return { minS, earn, disc, maxD };
        }
    }

    // 3. Percentage
    if (!earn) {
        const percMatch = tLow.match(/%(\d+)/);
        if (percMatch) {
            const rate = parseInt(percMatch[1]);
            const capMatch = tLow.match(/(?:en fazla|maksimum|max)\s*(\d+)\s*tl/);
            if (capMatch) {
                const cap = parseInt(capMatch[1]);
                maxD = cap;
                minS = Math.floor(cap * 100 / rate);
                earn = `${cap.toLocaleString('tr-TR')} TL İndirim`;
            } else {
                earn = `%${rate} İndirim`;
                const entry = tLow.match(/(\d+)\s*tl.*?alışveriş/);
                if (entry) minS = parseInt(entry[1]);
            }
        }
    }

    // 4. Points (Max Earning)
    const tierPattern = /(\d+)\s*tl.*?(\d+)\s*tl\s*(?:maxipuan|puan|indirim)/g;
    let bestEarn = 0;
    let bestSpend = 0;
    let match;
    while ((match = tierPattern.exec(tLow)) !== null) {
        const s = parseInt(match[1]);
        const e = parseInt(match[2]);
        if (s > e && e > bestEarn) {
            bestEarn = e;
            bestSpend = s;
        }
    }

    if (bestEarn > 0 && (maxD === 0 || bestEarn > maxD)) {
        maxD = bestEarn;
        minS = bestSpend;
        const suffix = titleLow.includes("indirim") ? "İndirim" : "MaxiPuan";
        earn = `${bestEarn.toLocaleString('tr-TR')} TL ${suffix}`;
    }

    // 5. Cyclic
    const unitMatch = tLow.match(/her\s*(\d+)\s*tl/);
    const totalMatch = tLow.match(/toplam(?:da)?\s*(\d+)\s*tl/);
    if (unitMatch && totalMatch) {
        const uSpend = parseInt(unitMatch[1]);
        const totalCap = parseInt(totalMatch[1]);
        const uEarnM = tLow.match(/(\d+)\s*tl\s*(?:maxipuan|puan)/);
        const uEarn = uEarnM ? parseInt(uEarnM[1]) : 0;

        if (uEarn > 0 && uEarn < totalCap) {
            const count = totalCap / uEarn;
            const calcSpend = Math.floor(count * uSpend);
            if (totalCap >= maxD) {
                maxD = totalCap;
                minS = calcSpend;
                const suffix = titleLow.includes("indirim") ? "İndirim" : "MaxiPuan";
                earn = `${totalCap.toLocaleString('tr-TR')} TL ${suffix}`;
            }
        }
    }

    return { minS, earn, disc, maxD };
};

export const extractParticipation = (text: string): string => {
    const methods: string[] = [];
    const tLow = trLower(text);

    if (tLow.includes("işcep") || tLow.includes("maximum mobil")) methods.push("Maximum Mobil / İşCep");
    const smsMatch = tLow.match(/([a-z0-9]+)\s*yazıp\s*(\d{4})/);
    if (smsMatch) methods.push(`SMS (${smsMatch[1].toUpperCase()} -> ${smsMatch[2]})`);

    if (tLow.includes("otomatik") && methods.length === 0) return "Otomatik Katılım";

    return methods.length > 0 ? Array.from(new Set(methods)).join(", ") : "Detayları İnceleyin";
};

