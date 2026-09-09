import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import Card from '../ui/Card';

const TemplateCard = ({ templateId, title, description }) => {
  const navigate = useNavigate();
  const { projectId } = useParams();

  return (
    <Card 
      title={title}
      description={description}
      hoverable
      onClick={() => navigate(`/projects/${projectId}/studio/${templateId}`)}
      className="mb-3 cursor-pointer"
    />
  );
};

export default TemplateCard;
