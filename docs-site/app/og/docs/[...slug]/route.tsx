import { getPageImageUrl, source } from '@/lib/source';
import { notFound } from 'next/navigation';
import { ImageResponse } from 'next/og';

export const revalidate = false;

/**
 * Branded OG card. Fumadocs ships a default generator, but it renders neutral chrome
 * with no mark, so a shared link looked like an untitled docs page rather than a
 * product. Drawn inline because `next/og` renders a restricted CSS subset.
 */
export async function GET(_req: Request, { params }: RouteContext<'/og/docs/[...slug]'>) {
  const { slug } = await params;
  const page = source.getPage(slug.slice(0, -1));
  if (!page) notFound();

  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          background: '#0B1020',
          padding: 72,
          fontFamily: 'sans-serif',
          color: '#fff',
        }}
      >
        {/* brand bar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <svg width="52" height="52" viewBox="0 0 32 32" fill="none">
            <defs>
              <linearGradient id="g" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
                <stop stopColor="#6366F1" />
                <stop offset="1" stopColor="#22D3EE" />
              </linearGradient>
            </defs>
            <rect width="32" height="32" rx="7.5" fill="url(#g)" />
            <g stroke="#fff" strokeWidth="2.1" strokeLinecap="round" fill="none">
              <path d="M6 9h4.5" />
              <path d="M6 16h4.5" />
              <path d="M6 23h4.5" />
              <path d="M10.5 9c4.2 0 3.4 7 7 7" />
              <path d="M10.5 23c4.2 0 3.4-7 7-7" />
              <path d="M10.5 16h7" />
              <path d="M17.5 16H26" />
            </g>
          </svg>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <span style={{ fontSize: 30, fontWeight: 600, letterSpacing: -0.5 }}>shipzil</span>
            <span style={{ fontSize: 17, color: '#94A3B8' }}>
              OpenRouter for Shipping
            </span>
          </div>
        </div>

        {/* page title */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          <span
            style={{
              fontSize: page.data.title.length > 28 ? 62 : 76,
              fontWeight: 600,
              letterSpacing: -1.6,
              lineHeight: 1.05,
            }}
          >
            {page.data.title}
          </span>
          {page.data.description ? (
            <span
              style={{
                fontSize: 27,
                color: '#94A3B8',
                lineHeight: 1.4,
                // next/og has no line-clamp; cap the text instead.
                maxWidth: 940,
              }}
            >
              {page.data.description.slice(0, 150)}
            </span>
          ) : null}
        </div>

        {/* footer */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, fontSize: 20 }}>
          <span
            style={{
              height: 6,
              width: 120,
              borderRadius: 999,
              background: 'linear-gradient(90deg,#6366F1,#22D3EE)',
            }}
          />
          <span style={{ color: '#64748B' }}>Python · Shippo · ShipStation · Easyship</span>
        </div>
      </div>
    ),
    { width: 1200, height: 630 },
  );
}

export function generateStaticParams() {
  return source.getPages().map((page) => ({
    lang: page.locale,
    slug: getPageImageUrl(page).segments,
  }));
}
