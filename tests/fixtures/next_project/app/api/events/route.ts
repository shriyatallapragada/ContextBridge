import { events } from '@/lib/events';
export function GET() { return Response.json(events); }
