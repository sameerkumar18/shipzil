import { source } from '@/lib/source';
import { basePath } from '@/lib/shared';
import { llms } from 'fumadocs-core/source';

export const revalidate = false;

export async function GET() {
  // `index()` returns a string on fumadocs-core 16.15.4 and a Promise from
  // 16.15.11. Awaiting works for both, since awaiting a plain string is a no-op.
  const index = await llms(source).index();
  return new Response(index.replaceAll('](/docs', `](${basePath}/docs`));
}
