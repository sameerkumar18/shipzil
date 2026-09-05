import { source } from '@/lib/source';
import { basePath } from '@/lib/shared';
import { llms } from 'fumadocs-core/source';

export const revalidate = false;

export function GET() {
  const index = llms(source).index().replaceAll('](/docs', `](${basePath}/docs`);
  return new Response(index);
}
