import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Upload } from 'lucide-react';
import Card from '../ui/Card';

const UploadDocumentCard = () => {
  const navigate = useNavigate();
  const { projectId } = useParams();

  return (
    <Card 
      hoverable
      onClick={() => navigate(`/projects/${projectId}/upload`)}
      className="mt-6 border-dashed border-2 bg-background/50 cursor-pointer"
    >
      <div className="flex flex-col items-center justify-center p-4 text-center">
        <Upload size={24} className="text-primary mb-2" />
        <h3 className="font-semibold text-gray-100">Upload Document</h3>
        <p className="text-sm text-gray-400 mt-1">Add a project document to your Sources.</p>
      </div>
    </Card>
  );
};

export default UploadDocumentCard;
