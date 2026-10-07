import { apiFetch } from "@/lib/api";
import { PageHeader } from "@/components/Card";
import FeedbackForm, { type FeedbackInfo } from "@/components/FeedbackForm";

export default async function FeedbackPage({ searchParams }: { searchParams: { from?: string } }) {
  const [info, diag] = await Promise.all([
    apiFetch<FeedbackInfo>("/api/feedback"),
    apiFetch<{ fields: { label: string; value: string }[] }>("/api/feedback/diagnostics"),
  ]);
  const from = searchParams.from && searchParams.from.startsWith("/") ? searchParams.from.slice(0, 200) : "";
  return (
    <div>
      <PageHeader title="Feedback" subtitle="Tell the PyXie maintainers what is broken, what is missing, or what works well" />
      <FeedbackForm info={info} serverFields={diag.fields} from={from} />
    </div>
  );
}
