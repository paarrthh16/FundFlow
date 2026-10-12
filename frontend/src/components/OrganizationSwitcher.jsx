import StatusMessage from "./StatusMessage.jsx";

// Organization selector. The organization list itself is loaded by App (tenant-neutral endpoint).
export default function OrganizationSwitcher({ organizations, selected, onSelect, onRetry }) {
  if (organizations.status === "loading") {
    return <StatusMessage>Loading organizations...</StatusMessage>;
  }
  if (organizations.status === "error") {
    return (
      <StatusMessage kind="error" onRetry={onRetry}>
        Could not load organizations: {organizations.error}
      </StatusMessage>
    );
  }
  if (organizations.items.length === 0) {
    return <StatusMessage>No organizations found. Run the seed command to create demo data.</StatusMessage>;
  }

  return (
    <div className="org-switcher">
      <label htmlFor="organization-select">Organization</label>
      <select id="organization-select" value={selected} onChange={(event) => onSelect(event.target.value)}>
        {organizations.items.map((org) => (
          <option key={org.slug} value={org.slug}>
            {org.name}
          </option>
        ))}
      </select>
    </div>
  );
}
