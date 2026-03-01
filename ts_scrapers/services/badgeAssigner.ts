/**
 * Badge Color Assignment for Campaigns
 * Automatically assigns badge text and color based on campaign type
 */

interface BadgeConfig {
  text: string;
  color: string;
}

const BADGE_COLORS = {
  TAKSIT: '#DCFCE7',
  MIL: '#DBEAFE',
  PUAN: '#F3E8FF',
  INDIRIM: '#FED7AA',
  DIGER: '#F1F5F9'
};

export function assignBadge(campaign: any): BadgeConfig {
  const earning = (campaign.earning || '').toLowerCase();
  const discount = (campaign.discount || '').toLowerCase();
  const title = (campaign.title || '').toLowerCase();
  const description = (campaign.description || '').toLowerCase();

  // TAKSİT - installment keywords
  if (
    title.includes('taksit') ||
    description.includes('taksit') ||
    earning.includes('taksit')
  ) {
    return { text: 'TAKSİT', color: BADGE_COLORS.TAKSIT };
  }

  // MİL - miles keywords
  if (
    earning.includes('mil') ||
    title.includes('mil') ||
    description.includes('mil')
  ) {
    return { text: 'MİL', color: BADGE_COLORS.MIL };
  }

  // PUAN - points keywords
  if (
    earning.includes('puan') ||
    earning.includes('worldpuan') ||
    title.includes('puan')
  ) {
    return { text: 'PUAN', color: BADGE_COLORS.PUAN };
  }

  // İNDİRİM - discount keywords
  if (
    discount ||
    title.includes('indirim') ||
    description.includes('indirim') ||
    earning.includes('indirim')
  ) {
    return { text: 'İNDİRİM', color: BADGE_COLORS.INDIRIM };
  }

  // DİĞER - fallback
  return { text: 'DİĞER', color: BADGE_COLORS.DIGER };
}
