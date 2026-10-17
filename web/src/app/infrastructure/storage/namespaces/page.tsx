import { PageHeader } from "@/components/Card";
import PbsNamespaces from "@/components/PbsNamespaces";
import { StorageIcon } from "@/components/Icons";

export default function NamespacesPage() {
  return (
    <div>
      <PageHeader title="Backup namespaces" subtitle="Storage · folders inside a backup server's datastores, so different policies can keep their backups apart with their own retention" icon={<StorageIcon className="w-5 h-5" />} />
      <PbsNamespaces />
    </div>
  );
}
