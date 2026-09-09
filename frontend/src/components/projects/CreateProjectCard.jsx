import React from 'react';
import { Plus } from 'lucide-react';
import Card from '../ui/Card';

const CreateProjectCard = ({ onClick }) => {
  return (
    <Card 
      onClick={onClick}
      className="h-full border-dashed border-2 hover:border-primary/50 hover:bg-surface-hover/50 flex flex-col items-center justify-center p-8 min-h-[220px]"
    >
      <div className="flex flex-col items-center justify-center text-center gap-3">
        <div className="p-3 bg-primary/10 rounded-full text-primary">
          <Plus size={32} />
        </div>
        <div>
          <h3 className="font-semibold text-gray-100 text-lg">Create new project</h3>
          <p className="text-sm text-gray-400 mt-1">Start a new project from scratch</p>
        </div>
      </div>
    </Card>
  );
};

export default CreateProjectCard;
