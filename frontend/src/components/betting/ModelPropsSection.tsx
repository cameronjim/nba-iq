export interface ModelPropLine {
  id: string;
  sentence: string;
}

interface ModelPropsSectionProps {
  lines?: ModelPropLine[];
}

export const ModelPropsSection = ({ lines = [] }: ModelPropsSectionProps): JSX.Element | null => {
  if (lines.length === 0) return null;

  return (
    <section aria-label="Model player props" className="space-y-1">
      <h3 className="text-sm font-semibold">Player props from the model</h3>
      <ul>
        {lines.map((line) => (
          <li key={line.id} className="text-sm py-1 border-t border-base-300 first:border-t-0">
            {line.sentence}
          </li>
        ))}
      </ul>
    </section>
  );
};
